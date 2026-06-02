#!/usr/bin/env python3
"""
GBrain History Sync for WhatsApp Conversations
Syncs WhatsApp conversation history from Hermes sessions to GBrain

Usage:
    python gbrain_history.py add <phone> <role> "<message>"
    python gbrain_history.py get <phone> [limit]
    python gbrain_history.py context <phone> [limit]
"""
import sys
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# GBrain database path
GBRAIN_DB = os.path.expanduser("~/.hermes/gbrain.db")

def get_db():
    """Get GBrain database connection"""
    os.makedirs(os.path.dirname(GBRAIN_DB), exist_ok=True)
    conn = sqlite3.connect(GBRAIN_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_phone ON whatsapp_history(phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON whatsapp_history(timestamp)")
    return conn

def add_message(phone: str, role: str, message: str):
    """Add a message to the history"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO whatsapp_history (phone, role, message) VALUES (?, ?, ?)",
        (phone, role, message)
    )
    conn.commit()
    conn.close()
    print(f"Added message to {phone}: [{role}] {message[:50]}...")

def get_history(phone: str, limit: int = 10):
    """Get history for a phone number"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, message, timestamp FROM whatsapp_history WHERE phone = ? ORDER BY timestamp DESC LIMIT ?",
        (phone, limit)
    )
    results = cursor.fetchall()
    conn.close()
    return results

def get_context(phone: str, limit: int = 10):
    """Get formatted context for loading into agent"""
    history = get_history(phone, limit)
    if not history:
        return f"No history found for {phone}"
    
    lines = [f"WhatsApp history for {phone}:"]
    for role, message, ts in reversed(history):
        lines.append(f"{role}: {message}")
    return "\n".join(lines)

def extract_phone_from_lid(lid: str) -> str:
    """Extract phone number from LID format (e.g., +94712345678@lid -> +94712345678)"""
    if not lid or "@" not in lid:
        return lid
    return lid.split("@")[0]

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "add":
        if len(sys.argv) < 5:
            print("Usage: python gbrain_history.py add <phone> <role> \"<message>\"")
            sys.exit(1)
        phone = sys.argv[2]
        role = sys.argv[3]
        message = sys.argv[4]
        add_message(phone, role, message)
    
    elif command == "get":
        if len(sys.argv) < 3:
            print("Usage: python gbrain_history.py get <phone> [limit]")
            sys.exit(1)
        phone = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        history = get_history(phone, limit)
        for role, message, ts in history:
            print(f"[{ts}] {role}: {message}")
    
    elif command == "context":
        if len(sys.argv) < 3:
            print("Usage: python gbrain_history.py context <phone> [limit]")
            sys.exit(1)
        phone = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        print(get_context(phone, limit))
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)

if __name__ == "__main__":
    main()