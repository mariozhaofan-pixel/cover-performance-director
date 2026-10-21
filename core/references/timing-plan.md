# 可选内部时间计划

多段覆盖、前奏偏移或重复乐句确需机械复算时，可创建项目内部JSON并运行[check_song_plan.py](../scripts/check_song_plan.py)。Python 3.10+，仅标准库；只读输入、输出JSON，不调用媒体、模型或外部服务。普通短稿不强制采用，JSON不代替面向用户的完整提示词。

下例为虚构自写测试唱词，不是实际音频证据：

```json
{
  "audio": {"id": "demo-v1", "role": "final", "duration": 12},
  "source_range": [0, 12],
  "offset": 1,
  "video_duration": 14,
  "max_segment_duration": 8,
  "require_verified_cues": false,
  "require_final_track": true,
  "lyrics": [{"id": "L01", "text": "风经过窗前"}, {"id": "L02", "text": "风经过窗前"}],
  "cues": [
    {"lyric_id": "L01", "start": 2, "end": 5, "evidence": "candidate"},
    {"lyric_id": "L02", "start": 7, "end": 10, "evidence": "candidate"}
  ],
  "segments": [
    {"id": "S01", "video_start": 0, "video_end": 7},
    {"id": "S02", "video_start": 7, "video_end": 14}
  ]
}
```

`audio.id`标识当前精确版本；`role`只可为reference或final。`source_range`默认整份音频，修改必须已有用户范围依据。`offset`将源时间映射到整片，即v=t+offset；不能通过负偏移静默丢掉原前奏。`video_duration`含完整所选音频与实际新增画面。

`lyrics`为本次范围的有序歌词，每次出现独立ID；`cues`与之一一对应且同序，start/end均为源音频时码。`audited`表示已有实际核对记录，`user-confirmed`表示用户已核准，`candidate`表示候选；标签由真实证据决定，脚本不认证这些声明。纯器乐范围两数组可为空。

`segments`按最终保留窗口无缝覆盖视频0至结尾。可选generate_start/generate_end表示更大的生成上下文，必须包住该保留窗口；重叠上下文在最终剪辑中裁去，不能重唱重叠部分。最大段长检查生成上下文长度，不只检查裁后长度；数值来自当前已核实模型，示例8秒不构成规格。

命令从技能根目录执行：独立版`python -B core/scripts/check_song_plan.py 项目计划.json`；导演版`python -B capabilities/cover-performance/scripts/check_song_plan.py 项目计划.json`。

成功退出只表示数值和所声明结构一致；`not_verified`明确实际音频、歌词正确性、证据真伪、音准、生成口型和平台能力均未验证。严格时码不能把candidate换名以求通过。`require_final_track`拦截把歌曲参考当最终音轨；仍不保证平台同步效果。跨乐句切段给出警告，需要检查相位与后期方案，不能忽略警告后宣称生成就绪。
