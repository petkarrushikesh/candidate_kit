# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv
# Activate the virtual environment for your shell.
pip install -r requirements.txt
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

Create a `.env` file in the project root:

```dotenv
MONGO_URI=mongodb+srv://<db_user>:<url_encoded_password>@<cluster-host>/?retryWrites=true&w=majority
MONGO_DB=hrone_attendance
```

Replace the placeholders with the MongoDB **database user's** credentials and the
cluster host from Atlas. URL-encode the password if it contains reserved URI
characters. Add your current IP address to the Atlas project's Network Access
list. Docker is not required when using Atlas; for a local MongoDB server,
install/start MongoDB or Docker and use `mongodb://localhost:27017` as `MONGO_URI`.
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
