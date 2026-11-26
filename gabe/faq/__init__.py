"""The FAQ bot: answers customers' questions from documents a company uploads.

- parse.py     reads FAQ documents (text, markdown, Word, CSV, JSON) into entries
- library.py   the folder of documents (config/faq/ by default)
- search.py    finds the entries that bear on a question; swappable per company
- answer.py    FaqResponder: drafts an answer from the FAQ only, or says there is none
- slack.py     the Slack front end (Socket Mode), escalation and the question log
- settings.py  the `faq` integration's settings in gabe.yaml

The `faq` integration (gabe/integrations/faq.py) turns it on and gives Gabe and the team a
`search_faq` action, so the same FAQ helps with email and chat too.
"""
