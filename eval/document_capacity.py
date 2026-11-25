"""Build distinct statute questions from independently located, numbered PDF headings."""
import json
import random
import re


def cases(generation,docs):
    headings=re.compile(r"^\s*§[ \t]*(\d+[a-z]?)[ \t]*\n[ \t]*([^\n]{4,105})",re.M)
    seen=set()
    entries=[]
    variants=[
        "Wie würdest du mir die Regelung in § {num} der Freiburger Vorschrift „{title}“ zum Thema „{section}“ erklären? Bitte nenne die Originalseite.",
        "Erkläre mir bitte anhand der Originalseite den Abschnitt „{section}“ (§ {num}) aus „{title}“ in Freiburg.",
        "Was bedeutet § {num} „{section}“ in „{title}“ für betroffene Personen? Erkläre es anhand der Freiburger Vorschrift.",
    ]
    for line in (generation/"pages.jsonl").read_bytes().splitlines():
        row=json.loads(line)
        doc=docs[row["doc"]]
        if doc["kind"]!="Ortsrecht":
            continue
        for match in headings.finditer(row["text"]):
            num=match[1]
            section=" ".join(match[2].split())
            key=row["doc"],num
            if (key in seen or not section[:1].isalpha() or len(section)>85 or section.endswith((",",":",";"))
                    or re.search(r"Inkrafttreten|Schlussbestimmung|Inhaltsverzeichnis|[.]{3,}",section,re.I)):
                continue
            seen.add(key)
            title=re.sub(r"^[\d.]+\s+","",doc["title"])
            question=variants[len(entries)%len(variants)].format(num=num,title=title,section=section)
            entries.append({"id":f"paragraph-{row['doc']}-{num}","question":question,
                            "doc":row["doc"],"page":row["page"],"patterns":[]})
    random.Random(20261125).shuffle(entries)
    return entries
