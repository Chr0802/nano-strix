# SQL Injection Analysis Guide

## Detection
- Look for string concatenation in SQL queries
- Check for f-strings used with database queries
- Identify unescaped user input in WHERE clauses
- Watch for dynamic table/column names from user input

## Common Patterns
- cursor.execute(f"SELECT * FROM users WHERE name='{user}'")
- query = "SELECT * FROM " + table_name
- raw SQL strings passed to ORM methods

## Recommendations
- Use parameterized queries (?, %s placeholders)
- Use ORM safely (no raw SQL unless necessary)
- Validate and sanitize all user input
