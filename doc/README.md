# 文档目录

本目录用于汇总 Synetra_DataFlow 的项目文档与规划索引。

## 索引

- **项目总览与快速开始**：仓库根 [`README.md`](../README.md)
- **需求 / 设计 / 规格（唯一权威来源）**：
  - 能力域规格：`openspec/specs/`
  - 已归档变更：`openspec/changes/archive/`（其中 `dataflow-v2` 为当前基线）
  - OpenSpec 规则与项目上下文：`openspec/config.yaml`
- **部署**：`deploy/install.sh`、`deploy/systemd/`
- **API 概览 / 配置项 / 测试**：见根 `README.md` 对应章节

## 规划原则

- 契约先行：规格、设计、任务与实现保持一致，proposal ↔ specs ↔ tasks 无断链。
- 中文书写：所有 OpenSpec 文档使用中文，需求用 **SHALL**，场景用 **WHEN / THEN**。
- 变更流程：先产出 change（proposal → specs → design → tasks），实现闭环后再 archive。
