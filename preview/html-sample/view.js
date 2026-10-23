fetch("data.json").then(r=>r.json()).then(data=>{document.getElementById("data").textContent=data.label});
