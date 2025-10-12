STEP-BY-STEP (ASAN):

1) Python install karo (3.8+). Terminal/command prompt kholo.

2) Project folder banao aur andar yeh files paste karo (jo upar diye gaye hain).

3) Virtual env:
   - Windows:
       python -m venv venv
       venv\Scripts\activate
   - Mac/Linux:
       python3 -m venv venv
       source venv/bin/activate

4) Install dependencies:
   pip install -r requirements.txt

5) (Optional: use MySQL) — agar MySQL use karna hai toh:
   - Install mysql server and create database, e.g. finance_db
   - Install driver `pip install mysqlclient` (ya `PyMySQL`)
   - Set environment variable before run:
       export DATABASE_URL='mysql://user:password@host/finance_db'
       export SECRET_KEY='your-secret'
       export ADMIN_USER='adminusername'
       export ADMIN_PASS='adminpass'
     (Windows PowerShell use $env:... syntax)

6) Run app:
   python app.py

7) Browser mein kholo:
   http://127.0.0.1:5000

8) Register karo, login karo, dashboard pe income/expenses/goals add karo.

Admin: agar tumne ADMIN_USER & ADMIN_PASS env vars set kiye hain to /admin/login se admin panel khul jayega.
--- END ---
