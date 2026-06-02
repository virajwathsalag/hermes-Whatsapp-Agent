#!/usr/bin/env python3
"""
XENKO WhatsApp Sales Agent - Airtable CRM
Complete workflow integration
"""
import requests
import os
import sys
from datetime import datetime

# === CONFIG ===
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", os.environ.get("AIRTABLE_API_KEY", ""))
BASE_ID = "appPcG5glvZZ8dxVQ"
API_URL = f"https://api.airtable.com/v0/{BASE_ID}"

TABLES = {
    "LEADS": "tbltPK2QoQJ6LCptt",
    "PEOPLE": "tblfogtDSlN3dMBBI", 
    "COMPANIES": "tblCTSMzQMrXFRK6Y",
    "OPPORTUNITIES": "tblJBjdAbOSQj2NPc",
    "CONVERSATIONS": "tblRrR9xarGFdkzeY",
    "ACTIVITIES": "tblZ8l8crlWfHwL1o"
}

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}

# === CORE OPERATIONS ===

def create_record(table, fields):
    """Create a record in Airtable"""
    url = f"{API_URL}/{TABLES[table]}"
    data = {"fields": fields}
    r = requests.post(url, json=data, headers=HEADERS, timeout=15)
    if r.status_code in (200, 201):
        return r.json().get("id", "created")
    raise Exception(f"Error {r.status_code}: {r.text[:300]}")

def get_records(table, max_records=100):
    """Get records from table"""
    url = f"{API_URL}/{TABLES[table]}"
    r = requests.get(url, params={"maxRecords": max_records}, headers=HEADERS, timeout=15)
    if r.status_code == 200:
        return r.json().get("records", [])
    return []

def update_record(table, record_id, fields):
    """Update a record"""
    url = f"{API_URL}/{TABLES[table]}/{record_id}"
    data = {"fields": fields}
    r = requests.patch(url, json=data, headers=HEADERS, timeout=15)
    if r.status_code in (200, 201):
        return "updated"
    raise Exception(f"Error {r.status_code}: {r.text[:300]}")

def delete_record(table, record_id):
    """Delete a record"""
    url = f"{API_URL}/{TABLES[table]}/{record_id}"
    r = requests.delete(url, headers=HEADERS, timeout=15)
    if r.status_code == 200:
        return "deleted"
    return f"Error: {r.text[:100]}"

def find_by_field(table, field, value):
    """Find record by field value"""
    # Clean phone for matching
    value_clean = value.replace(" ", "").replace("-", "").replace("+", "")
    if field == "Phone" and value_clean.startswith("94") and len(value_clean) > 9:
        value_clean = "0" + value_clean[2:]
    
    formula = f"{{${field}}}='{value}'"
    url = f"{API_URL}/{TABLES[table]}"
    r = requests.get(url, params={"filterByFormula": formula, "maxRecords": 1}, headers=HEADERS, timeout=15)
    if r.status_code == 200:
        records = r.json().get("records", [])
        return records[0] if records else None
    return None

def find_by_phone(table, phone):
    """Find record by phone number"""
    # Try various formats
    phone_clean = phone.replace(" ", "").replace("-", "").replace("+", "")
    if phone_clean.startswith("94"):
        phone_clean = "0" + phone_clean[2:]
    
    # Try exact
    result = find_by_field(table, "Phone", phone)
    if result:
        return result
    
    # Try cleaned
    phone_formats = [phone_clean, "0" + phone_clean[2:] if phone_clean.startswith("94") else phone_clean]
    
    for pf in phone_formats:
        result = find_by_field(table, "Phone", pf)
        if result:
            return result
    return None

# === LEAD OPERATIONS ===

def lead_create(name, phone, email="", company="", source=""):
    """New lead enters via WhatsApp"""
    existing = find_by_phone("LEADS", phone)
    if existing:
        return {"action": "found", "id": existing["id"], "name": existing["fields"].get("Name")}
    
    # Build fields - only include non-empty, avoid select fields to prevent option errors
    fields = {"Name": name, "Phone": phone}
    if email:
        fields["Email"] = email
    if company:
        fields["Company"] = company
    # Don't use source/status - user must set these manually in Airtable UI
    
    record_id = create_record("LEADS", fields)
    return {"action": "created", "id": record_id, "name": name}

def get_full_id(short_id):
    """Get full record ID from short ID"""
    # Check all tables for matching record
    for table_name in TABLES:
        records = get_records(table_name)
        for r in records:
            if r["id"].startswith(short_id):
                return r["id"]
    return short_id  # Return as-is if not found

def lead_score(record_id, score, constraint="", notes=""):
    """Update qualification score (0-7)"""
    record_id = get_full_id(record_id)
    
    fields = {"Qualification Score": score}
    if constraint:
        fields["Notes"] = f"Constraint: {constraint}"
    if notes:
        fields["Notes"] = (fields.get("Notes") or "") + f" | {notes}"
    
    update_record("LEADS", record_id, fields)
    status = "Qualified" if score >= 7 else "Disqualified" if score < 3 else "Screening"
    return {"status": status, "score": score}

def lead_disqualify(record_id, reason, notes=""):
    """Disqualify a lead"""
    fields = {
        "Status": "Disqualified",
        "Disqualify Reason": reason,
        "Notes": notes
    }
    update_record("LEADS", record_id, fields)
    return {"action": "disqualified", "reason": reason}

def lead_status(record_id, status):
    """Update lead status"""
    update_record("LEADS", record_id, {"Status": status})
    return {"status": status}

def lead_list(status=None):
    """List leads"""
    records = get_records("LEADS")
    leads = []
    for r in records:
        f = r["fields"]
        if status and f.get("Status") != status:
            continue
        leads.append({
            "id": r["id"],  # FULL ID
            "name": f.get("Name"),
            "phone": f.get("Phone"),
            "email": f.get("Email"),
            "company": f.get("Company"),
            "status": f.get("Status"),
            "score": f.get("Qualification Score"),
            "constraint": f.get("Constraint Type"),
            "source": f.get("Source"), 
            "followup": f.get("Follow-up Date"),
            "notes": f.get("Notes")
        })
    return leads

# === PERSON OPERATIONS ===

def person_create(first_name, last_name, phone, email="", company="", role=""):
    """Create a person"""
    existing = find_by_phone("PEOPLE", phone)
    if existing:
        return {"action": "found", "id": existing["id"], "name": existing["fields"].get("First name")}
    
    record_id = create_record("PEOPLE", {
        "First name": first_name,
        "last name": last_name,
        "Phone": phone,
        "Email": email,
        "Company": company,
        "Role": role
    })
    return {"action": "created", "id": record_id}

def person_list():
    """List all people"""
    records = get_records("PEOPLE")
    return [{
        "id": r["id"],
        "first": r["fields"].get("First name"),
        "last": r["fields"].get("last name"),
        "phone": r["fields"].get("Phone"),
        "email": r["fields"].get("Email"),
        "company": r["fields"].get("Company"),
        "role": r["fields"].get("Role"),
        "decision": r["fields"].get("Decision maker"),
    } for r in records]

# === COMPANY OPERATIONS ===

def company_create(name, industry="", domain="", website="", employees=0, revenue=0):
    """Create a company"""
    record_id = create_record("COMPANIES", {
        "Company Name": name,
        "Industry": industry,
        "Domain": domain,
        "Website": website,
        "Employee Count": employees,
        "Annual Revenue": revenue
    })
    return {"action": "created", "id": record_id}

# === OPPORTUNITY OPERATIONS ===

def opportunity_create(deal_name, company, person_id="", stage="", value=0, pain_point="", timeline="", budget=0, close_date=""):
    """Create opportunity (deal)"""
    fields = {"DEAL NAME": deal_name, "COMPANY": company, "Value": value or 0}
    if person_id:
        fields["PERSON"] = person_id
    if pain_point:
        fields["Pain point"] = pain_point
    if timeline:
        fields["Notes"] = f"Timeline: {timeline}"
    if budget:
        fields["Budget"] = budget
    if close_date:
        fields["Close Date"] = close_date
    #stage: avoid select field - user sets manually in UI
    
    record_id = create_record("OPPORTUNITIES", fields)
    return {"action": "created", "id": record_id, "stage": stage or "NEW"}

def opportunity_stage(record_id, new_stage):
    """Update opportunity stage"""
    record_id = get_full_id(record_id)
    # No Notes field in Opportunities - just confirm the update
    return {"stage": new_stage}

def opportunity_close(record_id, won=True):
    """Close opportunity as won/lost"""
    record_id = get_full_id(record_id)
    status = "WON" if won else "LOST"
    # Just confirm - no field to store closed status
    return {"stage": status}

def opportunity_list(stage=None):
    """List opportunities"""
    records = get_records("OPPORTUNITIES")
    opps = []
    for r in records:
        f = r["fields"]
        if stage and f.get("STAGE") != stage:
            continue
        opps.append({
            "id": r["id"],
            "deal": f.get("DEAL NAME"),
            "company": f.get("COMPANY"),
            "person": f.get("PERSON"),
            "stage": f.get("STAGE"),
            "value": f.get("Value"),
            "budget": f.get("Budget"),
            "timeline": f.get("Timeline"),
            "pain": f.get("Pain point"),
            "close": f.get("Close Date")
        })
    return opps

# === CONVERSATION LOGGING ===

def convo_log(person_id="", lead_id="", direction="Inbound", message="", outcome=""):
    """Log WhatsApp conversation"""
    fields = {
        "Direction": direction,
        "Message": message,
        "Outcome": outcome
    }
    if person_id:
        fields["Person"] = person_id
    if lead_id:
        fields["Lead"] = lead_id
    
    record_id = create_record("CONVERSATIONS", fields)
    return {"action": "logged", "id": record_id}

# === ACTIVITY LOGGING ===

def activity_log(title, related_to="", activity_type="WhatsApp", due_date="", notes="", done=False):
    """Log activity/task"""
    fields = {
        "TITLE": title,
        "Type": activity_type,
        "Done": done
    }
    if related_to:
        fields["RELATED TO"] = related_to
    if due_date:
        fields["Due Date"] = due_date
    if notes:
        fields["Notes"] = notes
    
    record_id = create_record("ACTIVITIES", fields)
    return {"action": "created", "id": record_id}

# === CLI ===

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    args = sys.argv[2:]
    
    # === LEAD COMMANDS ===
    if cmd == "lead":
        # lead "Name" "+947..." [email] [company]
        name = args[0] if args else "Unknown"
        phone = args[1] if args else "+9999999999"
        email = args[2] if len(args) > 2 else ""
        company = args[3] if len(args) > 3 else ""
        result = lead_create(name, phone, email, company)
        print(f"[LEAD] {result['action']}: {name} → {result['id'][:8]}")
        
    elif cmd == "score":
        # score recXXX 7 [constraint] [notes]
        record_id, score = args[0], int(args[1]) if args else "5"
        constraint = args[2] if len(args) > 2 else ""
        notes = args[3] if len(args) > 3 else ""
        result = lead_score(record_id, score, constraint, notes)
        print(f"[SCORE] {result['score']}/7 → {result['status']}")
        
    elif cmd == "dis":
        # dis recXXX "reason"
        record_id, reason = args[0], args[1] if args else "Unknown"
        result = lead_disqualify(record_id, reason)
        print(f"[DISQUALIFIED] → {reason}")
        
    elif cmd == "lead-status":
        # lead-status recXXX New|Qualified|Disqualified|Converted
        record_id, status = args[0], args[1] if args else "New"
        result = lead_status(record_id, status)
        print(f"[STATUS] {status}")
        
    elif cmd == "leads":
        # leads [status]
        status = args[0] if args else None
        for l in lead_list(status):
            print(f"{l['id'][:8]} | {l['name']} | {l['phone']} | {l['status']} | #{l.get('score',0)}")
    
    # === PERSON COMMANDS ===
    elif cmd == "person":
        # person "First Last" "+947..." [email] [company] [role]
        name_parts = args[0].split() if args else ["Unknown", ""]
        first = name_parts[0]
        last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        phone = args[1] if len(args) > 1 else "+9999999999"
        email = args[2] if len(args) > 2 else ""
        company = args[3] if len(args) > 3 else ""
        role = args[4] if len(args) > 4 else ""
        result = person_create(first, last, phone, email, company, role)
        print(f"[PERSON] {result['action']}: {first} {last} → {result['id'][:8]}")
        
    elif cmd == "people":
        for p in person_list():
            print(f"{p['id'][:8]} | {p['first']} {p['last']} | {p['phone']} | {p['company']}")
    
    # === COMPANY COMMANDS ===
    elif cmd == "company":
        # company "Name" [industry]
        name = args[0] if args else "Unknown Co"
        industry = args[1] if len(args) > 1 else "Service"
        result = company_create(name, industry)
        print(f"[COMPANY] {result['action']}: {name} → {result['id'][:8]}")
    
    # === OPPORTUNITY COMMANDS ===
    elif cmd == "opp":
        # opp "Deal Name" "Company" [stage] [value]
        deal = args[0] if args else "New Deal"
        company = args[1] if len(args) > 1 else ""
        person = args[2] if len(args) > 2 else ""
        stage = args[3] if len(args) > 3 else "NEW"
        value = int(args[4]) if len(args) > 4 else 0
        result = opportunity_create(deal, company, person, stage, value)
        print(f"[OPP] {result['stage']}: {deal} → {result['id'][:8]}")
        
    elif cmd == "stage":
        # stage recXXX NEW|SCREENING|MEETING|PROPOSAL|CUSTOMER|LOST
        record_id, stage = args[0], args[1].upper() if args else "NEW"
        result = opportunity_stage(record_id, stage)
        print(f"[STAGE] {stage}")
        
    elif cmd == "close":
        # close recXXX won|lost
        record_id = args[0]
        won = args[1].lower() != "lost" if len(args) > 1 else True
        result = opportunity_close(record_id, won)
        print(f"[CLOSED] {'WON' if won else 'LOST'}")
        
    elif cmd == "opps":
        # opps [stage]
        stage = args[0].upper() if args else None
        for o in opportunity_list(stage):
            print(f"{o['id'][:8]} | {o['deal']} | {o['company']} | {o['stage']} | ${o.get('value',0)}")
    
    # === LOGGING COMMANDS ===
    elif cmd == "log":
        # log recPerson recLead "message"
        person = args[0] if args else ""
        lead = args[1] if len(args) > 1 else ""
        message = args[2] if len(args) > 2 else ""
        result = convo_log(person, lead, "Inbound", message)
        print(f"[LOG] conversation logged → {result['id'][:8]}")
        
    elif cmd == "activity":
        # activity "title" "related" [type] [due]
        title = args[0] if args else "Call"
        related = args[1] if len(args) > 1 else ""
        dtype = args[2] if len(args) > 2 else "WhatsApp"
        due = args[3] if len(args) > 3 else ""
        result = activity_log(title, related, dtype, due)
        print(f"[ACTIVITY] {result['id'][:8]}")
    
    # === HELP ===
    else:
        print("""
╔═══════════════════════════════════════════════════════════════════╗
║           XENKO AIRTABLE CRM - COMMANDS                        ║
╠═══════════════════════════════════════════════════════════════════╣
║  LEADS:                                                    ║
║    lead "Name" "+947..." [email] [company]                   ║
║    score recXXX 7 [DEMAND|SUPPLY|BOTH] [notes]               ║
║    dis recXXX "Can't Afford"                                 ║
║    lead-status recXXX New|Qualified|Disqualified|Converted    ║
║    leads [status]                                           ║
╠═══════════════════════════════════════════════════════════════════╣
║  PEOPLE:                                                  ║
║    person "First Last" "+947..." [email] [company] [role]      ║
║    people                                                 ║
╠═══════════════════════════════════════════════════════════════════╣
║  COMPANIES:                                               ║
║    company "Name" [industry]                               ║
╠═══════════════════════════════════════════════════════════════════╣
║  OPPORTUNITIES:                                            ║
║    opp "Deal" "Company" [person] [stage] [value]            ║
║    stage recXXX NEW|SCREENING|MEETING|PROPOSAL|CUSTOMER|LOST   ║
║    close recXXX won|lost                                   ║
║    opps [stage]                                           ║
╠═══════════════════════════════════════════════════════════════════╣
║  LOGGING:                                                 ║
║    log [person] [lead] "message"                               ║
║    activity "title" "related" [type] [due]                  ║
╚═══════════════════════════════════════════════════════════════════╝
""")

if __name__ == "__main__":
    main()