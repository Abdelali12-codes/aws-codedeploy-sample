import os
from flask import Flask, jsonify, request
from hotfix_patch import apply_quiz_score_fix

app = Flask(__name__)

DRAINING_FLAG = "/var/www/my-app/draining"


@app.route("/")
def index():
    return jsonify({"message": "Quiz app v2", "hotfix": "applied"})


@app.route("/health")
def health():
    if os.path.exists(DRAINING_FLAG):
        return jsonify({"status": "draining"}), 503
    return jsonify({"status": "ok", "version": "2.0.0"})


@app.route("/score", methods=["POST"])
def score():
    # apply_quiz_score_fix caps score at 100 — the hotfix for the
    # off-by-one boundary condition bug
    raw = request.json.get("score", 0)
    fixed = apply_quiz_score_fix(raw)
    return jsonify({"raw_score": raw, "score": fixed})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
