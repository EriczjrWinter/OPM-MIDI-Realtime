# legacy —— 历史版本，仅供对比

这里的文件**不会**被播放器自动扫描到（界面只扫顶层目录），保留是为了做 A/B 对比
和追溯版本。

## FB01_GM_original.opm

最早的 `FB01_GM`，128 个 GM 槽位。由早期的池子筛选逻辑生成，存在问题：

- 5 组完全重复的槽位（其中两组是**跨家族错配**：45 号"震音弦乐"实际是 33 号贝斯，
  126 号"直升机"实际是 50 号弦乐合奏）
- 41% 的槽位来自游戏曲目 dump（含大量哥特风素材）
- 音色库声学审计：**42/128** 与 GM 家族预期不符

**已被 `FB01_GM_v7.opm` 取代**：

| | original | v7 |
|---|---|---|
| 重复槽位 | 5 组 | 0 |
| 声学不符 | 42/128 | 23/128 |
| 血统 | 混入 41% 游戏 dump | FB-01 71 + TX81Z 38 + X68K 18 + 街机 1 |

想做 A/B 的话：

```powershell
python rtplayer.py song.mid --bank FB01_GM_v7.opm            # 现在
python rtplayer.py song.mid --bank legacy\FB01_GM_original.opm  # 原始
```

生成流程与各版本的完整对比见 [../docs/DEVELOPMENT_zh.md](../docs/DEVELOPMENT_zh.md)。
