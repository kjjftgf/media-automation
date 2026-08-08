# 影视资源自动化入库管线 (Media Automation)

> 全自动影视入库系统：夸克分享链接 → TMDB 匹配 → 画质自动升级 → 转存夸克 → 规范化命名 → 飞书同步
> 本仓库仅开源**流程与架构**，所有真实凭据（夸克 cookie、TMDB key、飞书密钥、CASX 密码）均通过环境变量注入，不包含任何敏感信息。

---

## 🎯 系统概览

```
用户分享夸克链接
      │
      ▼
┌─────────────────┐
│  auto_import.py │  入口：解析分享 → TMDB 匹配 → 画质检查 → 转存 → 命名 → 飞书同步
└────────┬────────┘
         │
         ├──▶ TMDB API（电视剧/电影元数据，评分制智能匹配）
         ├──▶ 搜索管线（xiaokupan → PanSou → CloudSaver 三级回退）
         ├──▶ SearXNG 交叉验证（防图文不符）
         ├──▶ 夸克云盘 API（转存 / 建目录 / 重命名 / 移动 / 删除）
         ├──▶ CASX 任务系统（任务调度 / 快照去重 / 自动重命名）
         ├──▶ auto_unarchive 插件（zip 云解压救回压缩包源）
         └──▶ 飞书多维表格（📺剧集 / 🎞️动漫 / 🎬电影 三表同步）
```

## 📁 目录结构

```
media-automation/
├── core/                     # 核心管线
│   ├── auto_import.py        # 入库主脚本（分享链接 → 入库全流程）
│   ├── catchup.py            # 追更检查（检测新集自动入库）
│   ├── feishu_bitable.py     # 飞书多维表格同步（三表）
│   ├── quark_query.py        # 夸克网盘查询工具
│   └── dedup.py              # 重复文件清理
├── scripts/                  # 辅助/一次性脚本
│   ├── catchup_erlonghu*.py  # 单剧追更示例
│   ├── feishu_audit.py       # 飞书表格孤儿选项审计
│   ├── search_*.py           # 资源搜索示例
│   └── test_*.py             # API 测试示例
├── utils/
│   ├── docker_exec.py        # Docker 容器 exec 工具
│   └── monitor_docker.py     # 容器监控
├── .env.example              # 环境变量模板（复制为 .env 填写）
├── requirements.txt
└── README.md
```

## 🚀 快速开始

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的凭据

# 2. 安装依赖
pip install -r requirements.txt

# 3. 入库一部剧
python3 core/auto_import.py "https://pan.quark.cn/s/XXXXXX" --type tv --season 1

# 4. 追更检查
python3 core/catchup.py
```

## ⚙️ 环境变量

| 变量 | 用途 |
|------|------|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书开放平台凭据 |
| `BITABLE_APP_TOKEN` | 飞书多维表格 App Token |
| `CLOUDSAVER_HOST` / `CLOUDSAVER_ADMIN_CODE` | CloudSaver 搜索服务 |
| `PANSOU_BASE` / `PANSOU_HOST` | PanSou 搜索服务 |
| `SEARXNG_HOST` | SearXNG 交叉验证 |
| `CASX_HOST` / `CASX_ADMIN_PASSWORD` | CASX 任务系统 |

## 🧠 核心流程设计

### 1. TMDB 智能匹配
- 搜索候选 → **评分制选择**（标题相似度 + 年份 + 类型加权），非简单取第一条
- SearXNG 交叉验证：防止图文不符（例如"南部档案"搜出无关剧集）

### 2. 画质自动升级
- 入库前检查当前源质量，1080p 自动搜索同剧 4K/HDR/DV 版本
- 高分源逐个 `get_stoken` 实测有效性，能用的用最好的

### 3. 规范化命名
```
中文名 [tmdbid=XXX].SXXEXX.ext        # 剧集
中文名 [tmdbid=XXX].ext                # 电影
```
- `[tmdbid=XXX]` 标记是 VidHub 刮削识别的关键，不可省略
- 分季陷阱：TMDB 只有 S01 的剧，分享目录里的"S02"实际是 S01E13+，须按 TMDB seasons 结构定命名

### 4. 夸克云盘操作
- 转存用原生 POST（adapter.save_file 硬编码 pdir_fid 会挂嵌套目录）
- 转存后验证用 task API 的 `save_as_top_fids`，不信任 file/sort 分页
- **元数据验证**：转存后检查 `video_width > 0`，否则文件可能"占位但不可播放"

### 5. zip 源救活（auto_unarchive）
- 分享源是 zip/rar 压缩包时，用夸克云解压 API + CASX auto_unarchive 插件
- 流程：转存 zip → 云解压 → 单文件重命名 → 移动 + 清理
- 案例：南部档案 33 集 zip 源一次解压成功，全部 4K DV

### 6. 搜索管线（三级回退）
```
xiaokupan（优先，评分排序）→ PanSou → CloudSaver（备用）
```
- 集数提取只信精确匹配（`S01E05` 而非 `5`）
- 资源站比 TMDB 快 1 天，追更用它探测新集

## 🐛 已知陷阱

| 陷阱 | 现象 | 对策 |
|------|------|------|
| emoji/特殊字符文件名 | 文件被夸克分类为 `image`，不生成元数据，download 405 | 筛源时过滤 emoji 文件名 |
| 假视频文件 | 33 集全但 obj=image，不可播放 | 入库后必须验证 `video_width > 0` |
| 大文件元数据延迟 | >10GB 文件转存后元数据可能数小时才生成 | 入库后延时验证，不信任 task status=2 |
| OpenList 同名文件 | S03E020.mp4 和 S03E20.mp4 导致扫描挂起 | 统一 2 位集数命名，避免重复 |
| 302 重定向挂载 | 夸克 302 + OpenList 代理 → VidHub 播放失败 | 关闭 302 改代理模式 |

## 📄 License

MIT
