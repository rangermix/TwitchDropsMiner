"""Run the temporary native-browser login helper without starting the miner."""

from src.auth.login_helper import LoginHelperCLI


if __name__ == "__main__":
    LoginHelperCLI.main()
