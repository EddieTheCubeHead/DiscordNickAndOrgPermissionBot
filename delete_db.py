import os

if __name__ == "__main__":
    if "bot_db.sqlite" in os.listdir():
        os.remove("bot_db.sqlite")
