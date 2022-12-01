import os

if __name__ == "__main__":
    if "bot_db.sqlite" in os.listdir("./persistence"):
        os.remove("persistence/bot_db.sqlite")
