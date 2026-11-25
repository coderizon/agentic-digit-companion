"use strict";
const byId = id => document.getElementById(id);
const input = byId("frage");
const turns = [];
const examples = {
    schaden: "Unser Außendienst möchte kaputte Spielgeräte per Handy mit Foto und Standort melden. Gibt es dafür schon einen Service?",
    termin: "Wir möchten für unsere Sprechstunde freie Zeitfenster zur Online-Terminbuchung anbieten. Gibt es etwas Passendes?"
};
function paragraph(text) {
    const p = document.createElement("p");
    const tokens = /(\[[^\]\n]+\]\(\/api\/v1\/files\/[\da-f-]{36}\/content\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
    for (const piece of text.split(tokens)) {
        if (piece.startsWith("[") && /^\[[^\]\n]+\]\(\/api\/v1\/files\/[\da-f-]{36}\/content\)$/.test(piece)) {
            const end = piece.indexOf("](");
            const a = document.createElement("a");
            a.textContent = piece.slice(1, end);
            a.href = piece.slice(end + 2, -1);
            a.target = "_blank"; a.rel = "noopener noreferrer"; p.append(a);
        } else if (/^\*\*[^*]+\*\*$/.test(piece) || /^\*[^*]+\*$/.test(piece)) {
            const n = piece.startsWith("**") ? 2 : 1;
            const el = document.createElement(n === 2 ? "strong" : "em");
            el.textContent = piece.slice(n, -n); p.append(el);
        } else { p.append(document.createTextNode(piece)); }
    }
    return p;
}
function entry(role, text) {
    const article = document.createElement("article");
    article.dataset.rolle = role;
    const name = document.createElement("small"); name.textContent = role;
    article.append(name);
    for (const block of text.split(/\n\s*\n/)) {
        if (role === "Du") { const p = document.createElement("p"); p.textContent = block; article.append(p); }
        else { article.append(paragraph(block)); }
    }
    byId("verlauf").append(article);
    return article;
}
for (const button of document.querySelectorAll("[data-fall]")) {
    button.addEventListener("click", () => { input.value = examples[button.dataset.fall]; input.focus(); });
}
byId("neu").addEventListener("click", () => {
    turns.length = 0; byId("verlauf").replaceChildren(); input.value = ""; byId("status").textContent = "";
    byId("start").hidden = false; byId("neu").hidden = true; input.focus();
});
byId("formular").addEventListener("submit", async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || byId("senden").disabled) return;
    byId("senden").disabled = true; byId("neu").disabled = true; byId("status").textContent = "Quellen werden geprüft …";
    try {
        const response = await fetch("/bedarf", {method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({turns: [...turns, text].slice(-10)})});
        if (!response.ok) throw Error();
        const data = await response.json();
        turns.push(text); entry("Du", text); const answer = entry("Bedarfsassistent", data.antwort);
        byId("start").hidden = true; byId("neu").hidden = false; input.value = ""; byId("status").textContent = "";
        answer.scrollIntoView({block: "start"});
    } catch { byId("status").textContent = "Die lokale Demo ist nicht erreichbar. Bitte erneut versuchen."; }
    finally { byId("senden").disabled = false; byId("neu").disabled = false; }
});
input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); byId("formular").requestSubmit(); }
});
fetch("/info").then(r => r.json()).then(info => {
    byId("quelle").textContent = info.quelle + " · " + info.anzahl + " Quellen · lokal";
}).catch(() => { byId("status").textContent = "Die lokalen Quellen konnten nicht geladen werden."; });
