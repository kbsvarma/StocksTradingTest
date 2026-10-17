UI-only repair deployed; read REPORT.txt and ui_deployment.json. Trading code, flags and limits unchanged.
Audit scripts use read-only SQLite. collect.py is retained evidence of the STOPPED initial timing probe: do not restart it or run additional load tests.
Candidate and original UI modules retained. No recurring process added except the dashboard process shared quote worker.
