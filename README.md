# 翻唱表演导演

独立 Codex skill，版本 **1.2.0**。用户提供完整歌曲、角色板、音色参考或文字描述、场景与表演要求后，agent 调用包内工具分离人声和备用伴奏，生成歌词候选与节奏分段，再编译演唱视频提示词。

## 安装

在本仓库 Releases 下载 `cover-performance-director-2026-10-21-v1.2.0.zip`，解压后将整个 `cover-performance-director/` 放入宿主技能目录。Codex 默认目录为 `~/.codex/skills/`。升级前将旧版本备份到技能目录之外，再放入新目录。刷新技能或新开会话，用 `$cover-performance-director` 调用。完整说明见 [INSTALL.md](INSTALL.md)。

示例：“使用翻唱表演导演。这是完整歌曲、角色板、音色参考和场景，请自动分离音轨并编译整首翻唱提示词，伴奏备用。”

## 能力与边界

- 本地自动分离整曲人声和备用伴奏，原文件保持不变；首次运行按需建立隔离 Python 环境并下载模型。
- 生成歌词与词级时码候选，结合乐句、节奏规划连续片段。有歌名时检索公开歌词核对版本，保留实际录音的改词与重复；候选仍需核对。
- 视频默认每段最多30秒；同批参考音频默认共用30秒总额度，声线至少预留4秒，歌曲参考默认最多26秒。实际声线更长或有其他音频时继续扣减。
- 已有选段可按完整乐句截取或温和保调加速，歌词时码与备用伴奏同步变换；不靠补静音把上传音频撑到视频长度。
- 默认输出导演统一五栏，支持清唱、伴奏、弹唱、剧情内演唱和有界修订。声线控制身份，歌曲或最终歌声控制实际演唱时序。

分离的人声仍是原演唱者，不等于完成角色音色转换。新音色歌声和视频生成取决于实际可用工具；音频预处理或提示词完成不会被报告为成片完成。本仓库及安装包不包含项目歌曲、角色素材、模型权重、API 密钥或测试视频。

## 导演整合与维护

`core/` 是共用核心的唯一维护源。单独分发的导演整合版内置同一份核心，经 `knowledge/02I_COVER_PERFORMANCE.md` 接入原导演流程。安装导演包即可使用翻唱功能，无需再安装独立版；两者同时安装也可按调用名选择入口。本仓库的独立技能无需访问或安装导演整合版即可运行。

修改共用核心后，先同步再统一构建。输出目录必须在两份源码目录之外；已存在的发行文件不会被覆盖：

```powershell
python -X utf8 -B maintenance/sync_director.py --director ../director-seedance-prompt
python -X utf8 -B maintenance/sync_director.py --director ../director-seedance-prompt --check
python -X utf8 -B -m unittest discover -s core/scripts -p "test_*.py"
python -X utf8 -B maintenance/build_release.py --director ../director-seedance-prompt --output-dir ../release --version 2026-10-21-v1.2.0 --director-version 2026-10-21-v9
```

构建会校验三份安装包的文件清单、SHA256、本地链接、唯一技能入口，以及共用核心逐字节一致性。导演包中的哈希清单须与已审阅的源码一致。发布说明见 [CHANGELOG.md](CHANGELOG.md)。
