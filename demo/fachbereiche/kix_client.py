"""KIX 18 REST client for the local DIGIT service desk. No email transport."""
import json
from contextlib import contextmanager

import httpx


@contextmanager
def kix_session(config):
    with httpx.Client(base_url=config["api"].rstrip("/") + "/", trust_env=False,
                      timeout=httpx.Timeout(38, connect=8)) as client:
        response = client.post("auth", json={"UserLogin": config["login"],
                                             "Password": config["password"], "UserType": "Agent"})
        response.raise_for_status()
        client.headers["Authorization"] = "Token " + response.json()["Token"]
        try:
            yield client
        finally:
            try:
                client.delete("session", timeout=5)
            except httpx.HTTPError:
                pass


def get(client, resource, **params):
    response = client.get(resource, params=params)
    response.raise_for_status()
    return response.json()


def lookup(client, resource, object_name, field, value):
    filters = {object_name: {"AND": [{"Field": field, "Operator": "EQ", "Type": "STRING", "Value": value}]}}
    rows = get(client, resource, search=json.dumps(filters), limit=100).get(object_name, [])
    return [row for row in rows if str(row.get(field, "")) == str(value)]


def create_ticket(client, config, case):
    article = {"Subject": case["title"], "Body": case["description"],
               "ContentType": "text/plain; charset=utf-8", "Channel": "note", "SenderType": "agent"}
    ticket = {"Title": case["title"], "ContactID": config["contact_id"],
              "QueueID": config["queue_id"], "StateID": config["state_id"],
              "PriorityID": config["priority_id"], "TypeID": config["type_id"], "Articles": [article]}
    response = client.post("tickets", json={"Ticket": ticket})
    response.raise_for_status()
    return int(response.json()["TicketID"])


def confirmed_ticket(client, ticket_id, case):
    ticket = get(client, f"tickets/{int(ticket_id)}")["Ticket"]
    if ticket["Title"] != case["title"] or not str(ticket.get("TicketNumber", "")).isdigit():
        raise ValueError("KIX hat den erwarteten Vorgang noch nicht bestätigt.")
    return str(ticket["TicketNumber"])
