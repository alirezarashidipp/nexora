"use strict";

const byId = id => document.getElementById(id);
const chapters = ["overview", "question-policy", "p0", "p1", "p2"];
const capabilities = [
  ["Discover", "Find the right information", ["Locate the dashboard that holds the answer."]],
  ["Connect", "Bring data together", ["Add a source and review its structure."]],
  ["Explore", "Shape the analysis", ["Change charts and calculations interactively."]],
  ["Investigate", "Find an unexpected insight", ["Answer difficult questions beyond the dashboard."]],
  ["Simulate", "Test a portfolio decision", ["Vary headcount, team capacity and talent sourcing."]],
  ["Verify", "Demonstrate accuracy and value", ["Measure accuracy and compare with existing tools."]]
];
const degreesPerCapability = 360 / capabilities.length;

const ns = "http://www.w3.org/2000/svg";
function svgPart(parent, tag, attributes) {
  const el = document.createElementNS(ns, tag);
  Object.entries(attributes).forEach(([name,value]) => el.setAttribute(name, value));
  parent.append(el);
  return el;
}
function drawCover() {
  const svg = byId("orbit-lines");
  const group = svgPart(svg, "g", {fill:"none", stroke:"#dcdedb", "stroke-width":1});
  [274,307,340,455,483,598].forEach((r,i) => {
    const ring = svgPart(group,"circle",{cx:650,cy:290,r});
    if(i===1 || i===4) ring.setAttribute("stroke-dasharray","2 8");
  });
  for(let i=0;i<144;i++) {
    const a=i*Math.PI/72, r=i%6===0?354:344;
    svgPart(group,"line",{x1:650+340*Math.cos(a),y1:290+340*Math.sin(a),x2:650+r*Math.cos(a),y2:290+r*Math.sin(a)});
  }
  [[340,153],[340,-22],[455,23],[455,206],[274,-61],[483,-31]].forEach(([r,deg],i)=>{
    const a=deg*Math.PI/180, x=650+r*Math.cos(a), y=290+r*Math.sin(a);
    const dot = svgPart(group,"circle",{class:"orbit-dot",cx:x,cy:y,r:4,fill:i%2?"#9ca6a6":"#d60015",stroke:"#f8f8f6","stroke-width":2});
    dot.style.animationDuration = (r / 2) + "s";
  });
  for(const x of [99,1201]) for(const y of [103,490]){
    svgPart(group,"path",{d:"M"+(x-6)+" "+y+"h12M"+x+" "+(y-6)+"v12",stroke:"#b0b5b4"});
  }
}
drawCover();
function syncMotionVisibility() {
  document.documentElement.classList.toggle("motion-paused", document.hidden);
}
document.addEventListener("visibilitychange", syncMotionVisibility);
syncMotionVisibility();

let chapter = 0;
let mapOpen = false;
function selectCapability(index) {
  const [name,title,points] = capabilities[index];
  byId("cap-index").textContent = "0"+(index+1)+" / "+name.toUpperCase();
  byId("cap-title").textContent = title;
  byId("cap-points").replaceChildren(...points.map(point=>{
    const li = document.createElement("li"); li.textContent=point; return li;
  }));
  document.querySelectorAll(".node").forEach((node,i)=>node.setAttribute("aria-pressed", String(i===index)));
  byId("active-spoke").setAttribute("transform", `rotate(${index * degreesPerCapability})`);
  for (const animation of byId("signal-dot").getAnimations()) {
    animation.currentTime = 0;
  }
}
capabilities.forEach(([name],index)=>{
  const node = document.createElement("button");
  const angle = (index * degreesPerCapability - 90) * Math.PI / 180;
  node.className = "node";
  node.style.left = (50+39*Math.cos(angle))+"%";
  node.style.top = (50+39*Math.sin(angle))+"%";
  const number = document.createElement("small"); number.textContent="0"+(index+1);
  node.append(number,name.toUpperCase());
  node.onclick = ()=>selectCapability(index);
  byId("nodes").append(node);
});
function render() {
  const dest=location.hash.slice(1).toLowerCase();
  mapOpen=dest==="capabilities";
  chapter=Math.max(0,chapters.indexOf(dest));
  byId("cover").hidden=chapter!==0 || mapOpen;
  byId("map").hidden=!mapOpen;
  byId("secondary").hidden=chapter===0;
  if(mapOpen) selectCapability(0);
  if(chapter!==0) window.renderSection(chapters[chapter],byId("secondary"));
  document.querySelectorAll(".chapters a").forEach((a,i)=>{
    if(i===chapter) a.setAttribute("aria-current","page");
    else a.removeAttribute("aria-current");
  });
  byId("canvas").scrollTop=0;
}
function navigate(direction) {
  if(chapter===0 && !mapOpen && direction>0) location.hash="capabilities";
  else if(chapter===0 && mapOpen && direction<0) location.hash="overview";
  else if(chapter===1 && direction<0) location.hash="capabilities";
  else if(chapters[chapter+direction]) location.hash=chapters[chapter+direction];
}
byId("cover-trigger").onclick=byId("explore").onclick=()=>{location.hash="capabilities";};
byId("back").onclick=()=>{location.hash="overview";};
document.addEventListener("keydown",event=>{
  if(event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.target.closest("select,input,textarea,[role=tablist]")) return;
  if(event.key==="ArrowRight" || event.key==="ArrowLeft"){
    event.preventDefault();navigate(event.key==="ArrowRight"?1:-1);
  }
});
window.addEventListener("hashchange",()=>{render();byId("canvas").focus({preventScroll:true});});
render();
window.addEventListener("beforeprint", () => {
  const output = byId("print-deck");
  output.replaceChildren();
  const intro = document.createElement("section");
  intro.className = "print-page";
  intro.innerHTML = '<img src="hsbc.svg" alt="HSBC"><h1>NEXORA</h1><p class="expansion">Notice · Explain · eXplore · Optimise · Recommend · Act</p><div class="print-capabilities"></div>';
  capabilities.forEach(([name, title, points], i) => {
    const item = document.createElement("article");
    item.innerHTML = "<h3>0" + (i+1) + " — " + name + "</h3><strong>" + title + "</strong><ul>" + points.map(point=>"<li>"+point+"</li>").join("") + "</ul>";
    intro.lastElementChild.append(item);
  });
  output.append(intro);
  for(const key of chapters.slice(1)){
    const page=document.createElement("section");
    page.className="print-page";
    output.append(page);
    window.renderSection(key,page);
  }
});
window.addEventListener("afterprint",()=>byId("print-deck").replaceChildren());
