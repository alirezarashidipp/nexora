from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from .llm_connection import create_completion, llm_connection
from .database import describe_table, execute_sql
from .model_output import parse_output, response_text
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
