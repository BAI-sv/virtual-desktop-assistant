# -*- coding: utf-8 -*-
"""
把老整合包里的辅助权重拼装成【官方 IndexTTS 2.5】要的扁平结构。

背景（实测）：
  IndexTTS 的 `indextts2 download` 只下全了主模型（gpt.pth / s2mel.pth / qwen0.6bemo），
  辅助模型要么没下、要么卡在 ._____temp 里没搬出来：
      hf_cache/w2v-bert-2.0/model.safetensors   临时只到 0.47GB（完整 2.16GB）
      hf_cache/semantic_codec_model.safetensors  缺
      hf_cache/campplus_cn_common.bin            缺
      hf_cache/bigvgan/{config.json,bigvgan_generator.pt}  缺
  于是 `infer_v2_5.py` 走到
      Wav2Vec2BertModel.from_pretrained(.../hf_cache/w2v-bert-2.0, local_files_only=True)
  就抛 OSError: no file named model.safetensors。

好消息：老包 D:\\Index-tts-2.0-Windows-AMD\\checkpoints 里这些权重都有（HF cache 布局），
直接搬过来拼成扁平目录即可，不用重下。

用法： py -3.12 tools\\indextts_fix_aux.py
"""
import glob
import os
import shutil
import sys
import fairy_root

TARGET = os.path.join(fairy_root.INDEXTTS_ROOT, "checkpoints", "hf_cache")
OLDROOT = os.path.join(fairy_root.INDEXTTS_CANDIDATES[1], "checkpoints")


def find_one(pattern, min_mb=1.0):
    hits = [p for p in glob.glob(os.path.join(OLDROOT, pattern), recursive=True)
            if os.path.isfile(p) and os.path.getsize(p) >= min_mb * 2 ** 20]
    hits.sort(key=os.path.getsize, reverse=True)
    return hits[0] if hits else None


# (老包里的 glob, 目标相对路径, 说明, 最小体积MB)
PLAN = [
    ("hub/models--facebook--w2v-bert-2.0/snapshots/*/model.safetensors",
     "w2v-bert-2.0/model.safetensors", "w2v-bert 语义模型权重", 1000),
    ("hub/models--facebook--w2v-bert-2.0/snapshots/*/preprocessor_config.json",
     "w2v-bert-2.0/preprocessor_config.json", "w2v-bert 预处理配置", 0.001),
    ("hub/models--amphion--MaskGCT/snapshots/*/semantic_codec/model.safetensors",
     "semantic_codec_model.safetensors", "MaskGCT 语义码本", 100),
    ("hub/models--funasr--campplus/snapshots/*/campplus_cn_common.bin",
     "campplus_cn_common.bin", "campplus 说话人编码", 10),
    ("hub/models--nvidia--bigvgan_v2_22khz_80band_256x/snapshots/*/bigvgan_generator.pt",
     "bigvgan/bigvgan_generator.pt", "bigvgan 声码器", 100),
    ("hub/models--nvidia--bigvgan_v2_22khz_80band_256x/snapshots/*/config.json",
     "bigvgan/config.json", "bigvgan 配置", 0.001),
]


def main():
    print("目标 : %s" % TARGET)
    print("来源 : %s" % OLDROOT)
    if not os.path.isdir(OLDROOT):
        print("** 老包不存在，无法拼装 **")
        return 1
    ok = fail = skip = 0
    for pattern, rel, desc, min_mb in PLAN:
        dst = os.path.join(TARGET, rel.replace("/", os.sep))
        if os.path.exists(dst) and os.path.getsize(dst) >= min_mb * 2 ** 20:
            print("  [已有] %-42s %s" % (rel, desc))
            skip += 1
            continue
        src = find_one(pattern, min_mb)
        if not src:
            print("  [缺源] %-42s %s  （glob: %s）" % (rel, desc, pattern))
            fail += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print("  [已搬] %-42s %6.2f GB  <- %s"
              % (rel, os.path.getsize(dst) / 2 ** 30, os.path.basename(os.path.dirname(src))))
        ok += 1

    # 清掉没搬完的临时目录，免得误判
    for tmp in glob.glob(os.path.join(TARGET, "**", "._____temp"), recursive=True):
        try:
            shutil.rmtree(tmp)
            print("  [清理] 删除未完成的临时目录 %s" % tmp)
        except Exception as e:
            print("  [清理失败] %s: %s" % (tmp, e))

    print("\n结果：搬了 %d，已有 %d，缺源 %d" % (ok, skip, fail))
    # 复核
    print("\n复核官方 2.5 需要的文件：")
    for rel in ("w2v-bert-2.0/model.safetensors", "w2v-bert-2.0/config.json",
                "semantic_codec_model.safetensors", "campplus_cn_common.bin",
                "bigvgan/config.json", "bigvgan/bigvgan_generator.pt"):
        p = os.path.join(TARGET, rel.replace("/", os.sep))
        print("   %-42s %s" % (rel, ("OK %.2fGB" % (os.path.getsize(p) / 2 ** 30))
                               if os.path.exists(p) else "**仍缺**"))
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
