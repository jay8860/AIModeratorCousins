import sqlite3
import os

DATA_DIR = os.getenv("DATA_DIR", ".")
DB_FILE = os.path.join(DATA_DIR, "portfolio.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolios_v2 (
            chat_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            ticker TEXT,
            shares REAL,
            avg_price REAL,
            UNIQUE(chat_id, user_id, ticker)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cash_v2 (
            chat_id INTEGER,
            user_id INTEGER,
            balance REAL,
            UNIQUE(chat_id, user_id)
        )
    ''')
    conn.commit()
    conn.close()

def get_balance(chat_id: int, user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT balance FROM cash_v2 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    return 10000000.0  # Starting balance of ₹1 Crore

def update_balance(chat_id: int, user_id: int, new_balance: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO cash_v2 (chat_id, user_id, balance) VALUES (?, ?, ?)", (chat_id, user_id, new_balance))
    conn.commit()
    conn.close()

def get_portfolio(chat_id: int, user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT ticker, shares, avg_price FROM portfolios_v2 WHERE chat_id = ? AND user_id = ? AND shares > 0", (chat_id, user_id))
    rows = c.fetchall()
    conn.close()
    return [{"ticker": r[0], "shares": r[1], "avg_price": r[2]} for r in rows]

def buy_stock(chat_id: int, user_id: int, user_name: str, ticker: str, shares: float, price: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute("SELECT shares, avg_price FROM portfolios_v2 WHERE chat_id = ? AND user_id = ? AND ticker = ?", (chat_id, user_id, ticker))
    row = c.fetchone()
    
    if row:
        old_shares, old_price = row
        new_shares = old_shares + shares
        new_avg_price = ((old_shares * old_price) + (shares * price)) / new_shares
        c.execute("UPDATE portfolios_v2 SET shares = ?, avg_price = ? WHERE chat_id = ? AND user_id = ? AND ticker = ?", 
                  (new_shares, new_avg_price, chat_id, user_id, ticker))
    else:
        c.execute("INSERT INTO portfolios_v2 (chat_id, user_id, user_name, ticker, shares, avg_price) VALUES (?, ?, ?, ?, ?, ?)", 
                  (chat_id, user_id, user_name, ticker, shares, price))
        
    conn.commit()
    conn.close()

def get_all_investors(chat_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT DISTINCT user_id, user_name FROM portfolios_v2 WHERE chat_id = ?", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return [{"user_id": r[0], "user_name": r[1]} for r in rows]
