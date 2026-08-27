<p align="right"><a href="SECURITY.md">English</a> · <strong>中文</strong></p>

# 安全说明

请勿在公开 Issue 中提交可能暴露复核人员身份、邀请码、标注内容或认证材料的漏洞信息。此类问题请通过 GitHub 主页中的联系方式私下反馈。

共享部署应放在 HTTPS 反向代理后，设置 `ANNOTATION_COOKIE_SECURE=1`，并使用至少 32 字节熵的随机会话密钥。数据库、WAL、审计日志、导出文件、邀请码和会话密钥都应放在仓库之外。

公开截图前仍需人工检查图片和模型输出中是否包含个人或机密信息。
