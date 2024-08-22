from pydantic import BaseModel


class Secret(BaseModel):
    secret_key: str

    github_oauth_client_id: str
    github_oauth_client_secret: str

    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str

    @staticmethod
    def parse(**kwargs):
        # replace - with _ and . with _
        return Secret(**{k.replace("-", "_").replace(".", "_"): v for k, v in kwargs.items()})
