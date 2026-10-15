"""安全基础设施包。

目前包含：
- :mod:`app.security.passwords` —— 密码哈希（Argon2id）与强度校验。

本包只放"安全原语"（密码、令牌、随机数），不放业务流程；
业务流程见 :mod:`app.services.auth`。
"""
