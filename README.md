# Lateness Application
#### Description: Lateness Application is a simple disciplinary reporting tool that helps track boarder lateness over time. It accepts monthly CSV log files, maps each record to a master student list, computes lateness occurrences and penalty points, and saves the results in an SQLite database. The web dashboard lets users search past records, view saved reports, download CSV exports, and remove outdated months.

- Upload monthly CSV attendance logs
- Match entries against a master boarder list (namelist.csv)
- Calculate lateness frequency and total late minutes
- Store monthly summaries in SQLite (lateness_history.db)
- Search historical boarder reports by name
- View, download, and delete saved month reports
- UI powered by templates/index.html, backend logic in app.py, and parsing utilities in parser.py
