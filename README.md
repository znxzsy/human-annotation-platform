<div align="center">
  <img src="assets/hero.svg" alt="人工标注平台：面向强化学习与大模型训练的数据标注平台" width="100%">

  <br>

  [![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-315d88?logo=python&logoColor=white)](https://www.python.org/)
  [![SQLite](https://img.shields.io/badge/SQLite-durable-1d7a55?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
  [![Tests](https://img.shields.io/badge/tests-stdlib_only-e3a94f)](#测试)
  [![License: MIT](https://img.shields.io/badge/license-MIT-7a639d)](LICENSE)
</div>

做强化学习、大模型微调或模型评测时，难点通常出在多人协作：几十个人要按同一套规则标注和复核，每条数据还得说清楚由谁处理、改过几次、最后为什么被采用。

**人工标注平台**是一套开源的多人数据标注与质量管理工具。图片、模型输出、人工修正和复核结果都能放进同一个项目里处理，适合构建 RLHF、人类反馈、SFT、DPO、KTO 和模型评测数据。平台支持自动保存、冲突保护、实名邀请码、二次复核、质量榜单和可审计导出。

不需要前端工程环境。Python 标准库加 SQLite，一条命令就能跑起来；先用演示数据试手感，再接入自己的数据。

> 正在搭标注系统或整理强化学习数据？可以直接 Fork 改造。觉得有用，也欢迎点个 Star，让更多做数据的人看到它。

> 仓库中的图片、姓名、模型结果与统计数据均为程序生成的演示内容，不含真实业务数据。

## 先跑起来

```bash
git clone https://github.com/znxzsy/human-annotation-platform.git
cd human-annotation-platform
./scripts/run_demo.sh
```

浏览器打开 [http://127.0.0.1:18068](http://127.0.0.1:18068)。首次启动会生成 12 组模拟数据，包含已完成、部分完成、需修正和无法判断等情况，可以直接体验标注、复核和统计流程。

也可以使用 Docker：

```bash
docker compose up --build
```

## 适合哪些任务

### 强化学习与大模型数据标注

- 支持图片、模型回答和人工修正，适合整理人类反馈与偏好数据。
- 每条结果可以单独判断正确、错误、图片残缺、无手写或无法判断。
- 错误结果可以直接填写人工修正，后续复核不会覆盖原始记录。
- 正常、需修正和待确认的数据会自动进入各自的复核池。

### 多人协作标注

- 前端自动保存草稿，刷新和临时断网都不轻易丢数据。
- 服务端使用版本号与幂等键，拦住旧页面覆盖新结果。
- 已完成的数据仍可返回修改，每条记录都保留标注人和修改时间。
- SQLite 开启 WAL、完整同步与写入锁，单机部署也能稳妥支撑多人协作。

### 质量复核与数据导出

- 主看板展示总量、完成量、剩余量和每个人的工作进度。
- 二次复核按正常、需修正和待确认分池，可随机抽样，也可指定批次。
- 榜单可以按标注量或准确率排序，也能分别统计标注和复核工作量。
- 导出快照自带 manifest、SHA256 与 `FROZEN_OK`，适合继续构建 SFT、DPO、KTO 数据。

## 界面

<!-- Screenshots are generated from synthetic demo data. -->

| 质量看板 | 人工标注工作台 |
| --- | --- |
| ![质量看板](assets/dashboard.png) | ![人工标注工作台](assets/review.png) |

## 数据怎么流动

```mermaid
flowchart LR
  A[模型输出与图片] --> B[导入并冻结 event_id]
  B --> C[逐条人工标注]
  C --> D{结论}
  D -->|正确| E[正常数据复核池]
  D -->|错误 + 修正| F[需修正数据复核池]
  D -->|模糊 / 无手写 / 无法判断| G[待确认数据复核池]
  E --> H[质量榜单]
  F --> H
  G --> H
  H --> I[带哈希的训练快照]
```

每条标注结果都有独立主键、版本号和修改记录。批次进度来自实际标注结果汇总，局部修改不会清空同批次的其他数据。

## 导入自己的五题数据

导入器接受 `details_*.json` 分片。每个 request 需要提供五个 `slot_indices`、图片引用和模型原始输出：

```json
[
  {
    "page_id": "page-demo",
    "slot_items": [
      {"slot_index": 1, "title": "两位数加法"}
    ],
    "requests": [
      {
        "id": "request-demo",
        "slot_indices": [1, 2, 3, 4, 5],
        "image_url": "images/demo.svg",
        "model_raw_content": "[{\"r\":\"38+27=65\",\"h\":0},{\"r\":\"72-19=53\",\"h\":0},{\"r\":\"6\\\\times8=48\",\"h\":0},{\"r\":\"56\\\\div7=8\",\"h\":0},{\"r\":\"45+18=63\",\"h\":0}]"
      }
    ]
  }
]
```

```bash
python -m annotation_platform.importer \
  --details-dir ./my-details \
  --output-dir ./runtime/imported

python -m annotation_platform.server \
  --registry ./runtime/imported/source_groups.jsonl \
  --db ./runtime/review.sqlite3 \
  --audit ./runtime/audit.jsonl
```

导入过程会拒绝不安全的本地图片路径、检查五题结构、计算源文件 SHA256，并生成可复查的 manifest。

## 多人部署与实名邀请码

本地 Demo 默认关闭身份认证。共享部署时，先生成会话密钥与一次性邀请码：

```bash
mkdir -p secrets runtime
python -c 'import secrets; print(secrets.token_hex(32))' > secrets/session-secret
python scripts/generate_invites.py --count 30
```

然后启用认证：

```bash
export ANNOTATION_AUTH_REQUIRED=1
export ANNOTATION_SESSION_SECRET_FILE=secrets/session-secret
export ANNOTATION_COOKIE_SECURE=1
./scripts/run_demo.sh
```

邀请码首次使用时绑定姓名，服务端只保存邀请码哈希。`runtime/invites.txt` 含明文邀请码，应通过私密渠道分发并在分发后妥善保管，绝不要提交到 Git。

## 目录

```text
annotation_platform/
├── annotation_platform/   # HTTP 服务、SQLite Store、导入与导出
│   └── static/            # 无框架前端
├── scripts/               # Demo 数据与邀请码工具
├── tests/                 # 并发安全与页面冒烟测试
├── assets/                # README 视觉素材
└── runtime/               # 本地数据库与审计日志（默认忽略）
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试只使用 Python 标准库，覆盖部分未标筛选、空占位保护、演示数据库、HTTP API 与主要页面。提交前也建议执行：

```bash
python -m compileall -q annotation_platform scripts tests
```

## 安全边界

- 不要把真实标注数据库、审计日志、邀请码、学生图片或身份映射提交到仓库。
- `.gitignore` 默认屏蔽 SQLite、JSONL、密钥、导出目录和运行时数据。
- 生产环境请置于 HTTPS 反向代理后，并设置 `ANNOTATION_COOKIE_SECURE=1`。
- 平台不会替你完成数据脱敏；公开任何截图或样本前仍需人工复查。

## 为什么开源

标注工具常常从一张临时页面开始，等数据量上来以后，才发现最贵的不是页面，而是丢失的归属、模糊的口径和无法解释的统计。这个项目保留了真实协作中踩过坑后长出来的结构，希望让下一套标注任务不必从同一个坑里重新爬一次。

如果它对你有用，欢迎提 Issue、补充新的复核策略，或者把它改造成适合你数据格式的版本。

## License

[MIT](LICENSE)
