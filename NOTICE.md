# 来源与授权说明

## 上游项目与原作者归属

1. **原始白龙桌宠项目**：https://github.com/jingh12312-rgb/bailong-desktop-pet
   - 提供原始白龙角色形象、初始 15 段动画、后续 v0.4.1 的 10 段新动作以及基础桌面宠物程序框架。
2. **上游 Codex 增强版原作者（Wunsch2048）**：https://github.com/Wunsch2048/codex-bailong-desktop-pet
   - 以白龙桌宠 v0.3.3 为改造基线（并合入 v0.4.1 十段新动作），构建了 Codex 联动、水球状态气泡、额度与 Token 统计日报、独立素材分发、视频全屏让位及完整部署架构（截至公开版本 v1.0.1）。
3. **macOS 社区移植基线（Kki1ds）**：https://github.com/Kki1ds/codex-bailong-desktop-pet
   - 基于 Wunsch2048 v1.0.1 提供了 Apple Silicon macOS 平台适配与 `.app` / `.dmg` 构建流程（`1.0.1-mac.1`）。
4. **当前分支扩展（jyfjyta/codex-bailong-desktop-pet）**：https://github.com/jyfjyta/codex-bailong-desktop-pet
   - 在上述上游作品基础上新增 Live2D Q版打字伴侣（键盘/鼠标实时同步、10 种互动表情与系统密码防窥）、Codex / Antigravity 双源切换与 `agy` CLI 实时 4 项额度同步，以及 Windows 10/11 与 macOS 的跨平台适配。

## 授权与再分发边界

上游发布者于 2026-10-21 确认已取得原始白龙作者对源码、角色素材和安装包公开再分发的授权。此记录仅是上游发布者的确认，不代表原作者向所有访问者另行授予权利，也不覆盖第三方组件。原项目未附统一代码许可证；请勿因仓库公开而推定为 MIT、可自由商用或不受限制的再分发。

原素材声明完整保留在 [docs/upstream/ASSETS.md](docs/upstream/ASSETS.md)，上游 v0.4.1 的素材声明另原样保留在 [docs/upstream/ASSETS-v0.4.1.md](docs/upstream/ASSETS-v0.4.1.md)。角色动画随上游 Release 分发，不放入 Git 历史。若希望另作商业使用、改编或再分发，请向相应权利人确认许可范围。

## 第三方组件与隐私声明

- 字体 Chakra Petch 来自 Google Fonts，按随附 SIL Open Font License 使用。
- Qt/PySide6、Pillow、msgspec、comtypes 等依赖按各自许可证分发。`app/licenses` 保存已有组件声明；打包运行库还包含其自身许可文件。本 NOTICE 不替代这些许可。
- UI 图表截图、额度看板预览与 GIF 均使用隔离合成的演示数据，不含真实会话原文、账号信息或 API 密钥。
