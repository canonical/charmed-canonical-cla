from pydantic import BaseModel


class Secret(BaseModel):
    secret_key: str

    github_oauth_client_id: str
    github_oauth_client_secret: str

    github_app_id: str
    github_app_private_key: str
    github_app_secret: str
    
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str

    db_host: str | None
    db_port: int | None
    db_name: str | None
    db_username: str | None
    db_password: str | None

    @staticmethod
    def parse(**kwargs):
        # replace - with _ and . with _
        return Secret(**{k.replace("-", "_").replace(".", "_"): v for k, v in kwargs.items()})
