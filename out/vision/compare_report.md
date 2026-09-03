# VLM 交叉校验报告

## 水库基本信息

- [OK] **百年一遇泄量** (`1454 m³/s`) → matched: `1454`
- [OK] **千年一遇泄量** (`2218 m³/s`) → matched: `2218`
- [OK] **汛限水位** (`788.5 m`) → matched: `788.5`

## 大坝剖面图

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [OK] **汛限水位** (`788.5 m`) → matched: `788.5`

## 溢洪道图1

- [OK] **百年一遇泄量** (`1454 m³/s`) → matched: `1454 m³/s`
- [OK] **千年一遇泄量** (`2218 m³/s`) → matched: `2218 m³/s`
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 溢洪道图2

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 库容水位对照表

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 泄流曲线

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [OK] **汛限水位** (`788.5 m`) → matched: `788.5`

## 物资图1

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 物资图2

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 三个责任人

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 中心架构图

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 大坝注册登记证

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``

## 各部门用水需求

- [MISSING] **百年一遇泄量** (`1454 m³/s`) → matched: ``
- [MISSING] **千年一遇泄量** (`2218 m³/s`) → matched: ``
- [MISSING] **汛限水位** (`788.5 m`) → matched: ``


---
*运行前请先人工审查 VLM raw 输出，再用 `--approve` 标记审核通过。*

---
**2026-08-27 补录**：库容水位对照表于首次全量运行中三次超时失败（网关当时受其他服务干扰）；单图重试一次成功（114s），命中汛限水位 788.5。上表中该图的 MISSING 行已过时，以同目录 `库容水位对照表_raw.txt` / `_cross_check.json` 为准。
