from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError

from .llm_connection import create_completion, llm_connection
from .database import describe_table, execute_sql
from .prompts import load_prompt, render_prompt

ReviewStatus = Literal["valid", "incomplete", "invalid"]


class WorkflowState(TypedDict):
    question: str
    schema: NotRequired[str]
    status: NotRequired[ReviewStatus]
    normalized_question: NotRequired[str]
    message: NotRequired[str]
    sql: NotRequired[str]
    rows: NotRequired[list[dict]]
    answer: NotRequired[str]


class QuestionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    normalized_question: str
    message: str


class SqlQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str


def response_text(response) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        raise ValueError("The model returned no choices.")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if getattr(choice, "finish_reason", None) != "stop" or getattr(message, "refusal", None):
        raise ValueError("The model refused or did not complete the response.")
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model returned no content.")
    return content


def parse_output(response, output_type):
    try:
        return output_type.model_validate_json(response_text(response))
    except ValidationError:
        raise ValueError("The model returned an invalid structured response.") from None


def review_question(state: WorkflowState) -> dict:
    schema = describe_table()
    with llm_connection() as (client, model):
        response = create_completion(
            client, model,
            render_prompt("question_review", schema=schema),
            state["question"],
            json_schema=QuestionReview.model_json_schema(),
        )
    review = parse_output(response, QuestionReview)
    update = {"schema": schema, **review.model_dump()}
    if review.status != "valid":
        update["answer"] = review.message
    return update


def route_after_review(state: WorkflowState) -> Literal["query_database", END]:
    return "query_database" if state["status"] == "valid" else END


def query_database(state: WorkflowState) -> dict:
    with llm_connection() as (client, model):
        response = create_completion(
            client, model,
            render_prompt("sql_generation", schema=state["schema"]),
            state["normalized_question"],
            json_schema=SqlQuery.model_json_schema(),
        )
    sql = parse_output(response, SqlQuery).sql.strip()
    return {"sql": sql, "rows": execute_sql(sql)}


def generate_answer(state: WorkflowState) -> dict:
    with llm_connection() as (client, model):
        response = create_completion(
            client, model, load_prompt("answer"),
            {
                "question": state["normalized_question"],
                "sql": state["sql"],
                "rows": state["rows"],
            },
        )
    return {"answer": response_text(response)}


builder = StateGraph(WorkflowState)
builder.add_node("review_question", review_question)
builder.add_node("query_database", query_database)
builder.add_node("generate_answer", generate_answer)
builder.add_edge(START, "review_question")
builder.add_conditional_edges("review_question", route_after_review)
builder.add_edge("query_database", "generate_answer")
builder.add_edge("generate_answer", END)
question_graph = builder.compile()
