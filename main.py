from pathlib import Path

import questionary  # you'd need to add this to pyproject.toml

from src.deepsek import solver


def setup_env():
    print("Welcome! Let's set up your .env file.")
    keys = questionary.text("Enter your Groq API Key(s) (comma separated):").ask()
    with open(".env", "w") as f:
        f.write(f"GROQ_API_KEYS={keys}\n")
        f.write("GROQ_MODEL=openai/gpt-oss-120b\n")
    print("✅ .env created successfully!")


def main():
    env_file = Path(".env")
    if not env_file.exists() or env_file.stat().st_size == 0:
        setup_env()

    # Pass control to the actual solver
    solver.main()


if __name__ == "__main__":
    main()
