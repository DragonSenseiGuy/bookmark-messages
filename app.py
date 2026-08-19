from flask import Flask, request, abort

app = Flask(__name__)

@app.route("/submit", methods=["POST"])
def submit():
    uploaded = request.files.get("file")
    if uploaded:
        if not uploaded.filename.lower().endswith(".txt"):
            abort(400, "expected a .txt file")
        text = uploaded.read().decode("utf-8")
    else:
        if request.content_type and request.content_type.startswith("text"):
            text = request.get_data(as_text=True)
        else:
            abort(400, "no file or text/plain body provided")
    with open("data/messages.txt", "w", encoding="utf-8") as f:
        f.write(text)

    return "OK", 200


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)