# SQLite to MySQL Migration

The app still uses SQLite by default. Set `DB_ENGINE=mysql` to use MySQL.

## 1. Install MySQL driver

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mysql.txt
```

## 2. Create an empty MySQL database

Use `utf8mb4`:

```sql
CREATE DATABASE inventory_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## 3. Set database environment variables

```powershell
$env:DB_ENGINE = "mysql"
$env:MYSQL_DATABASE = "inventory_db"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "your_mysql_password"
$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
```

## 4. Create all MySQL tables

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

## 5. Load existing SQLite data

The current SQLite data was exported to:

```text
backups\sqlite_to_mysql_data.json
```

Load it into MySQL:

```powershell
.\.venv\Scripts\python.exe manage.py loaddata backups\sqlite_to_mysql_data.json
```

## 6. Run the server on MySQL

Keep the same environment variables set:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```
