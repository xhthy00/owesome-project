# DB-GPT Agent 自主规划实现机制

本文档详细描述了 DB-GPT 项目中 Agent 自主规划的实现机制。

## 1. 概述

DB-GPT 的 Agent 自主规划采用 **Plan-Execute（规划-执行）** 模式，结合 **ReAct** 推理模式，实现了一个完整的自主智能体系统。该系统能够：

- 将复杂任务分解为可执行的子任务
- 管理任务依赖关系和执行顺序
- 通过反思和重试机制提升执行质量
- 支持多 Agent 协作

## 2. 核心架构

### 2.1 Agent 继承层次

```
Agent (抽象接口)
    │
    └── ConversableAgent (核心实现类)
            │
            ├── ReActAgent (ReAct 模式 Agent)
            ├── PlannerAgent (任务规划 Agent)
            └── ManagerAgent (团队管理 Agent)
                    │
                    └── AutoPlanChatManager (自动规划团队管理)
                            │
                            └── AWELBaseManager (AWEL 布局管理)
```

### 2.2 核心目录结构

```
packages/dbgpt-core/src/dbgpt/agent/
├── core/                       # 核心模块
│   ├── base_agent.py           # ConversableAgent 核心实现
│   ├── agent.py                # Agent 接口定义
│   ├── role.py                 # 角色定义
│   ├── action/                 # Action 基类和实现
│   ├── memory/                 # 记忆系统
│   │   └── gpts/               #   - GptsMemory, GptsPlan
│   └── plan/                   # 规划模块
│       ├── team_auto_plan.py    #   - AutoPlanChatManager
│       ├── planner_agent.py     #   - PlannerAgent
│       └── plan_action.py       #   - PlanAction
├── expand/                     # 扩展实现
│   └── react_agent.py          # ReActAgent 实现
└── util/                       # 工具模块
    └── react_parser.py         # ReAct 输出解析器
```

## 3. 核心执行循环

### 3.1 ConversableAgent.generate_reply

ConversableAgent 是所有可对话 Agent 的基类，其 `generate_reply` 方法实现了核心执行循环。

**文件位置**: `agent/core/base_agent.py`

**执行流程**:

```
┌─────────────────────────────────────────────────────────────┐
│                    generate_reply 循环                       │
│                    (max_retry_count 次)                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   1. thinking() ──► 2. review() ──► 3. act()               │
│         │                │                │                 │
│         ▼                ▼                ▼                 │
│    LLM 推理生成      审核内容合法性    解析并执行 Action       │
│    思考内容                                    │              │
│                                               ▼              │
│   5. 自优化 ◄──────── 4. verify() ◄──── 执行结果验证        │
│         │                                   │               │
│         ▼                                   ▼               │
│    失败?记录记忆                       check_pass            │
│    重试或放弃                                 │               │
│                                               ▼              │
│                                          成功?               │
│                                            │                │
│                         ┌──────────────────┴──────────────┐│
│                         ▼                                  ││
│                   返回最终结果                               ││
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**关键方法**:

| 方法 | 作用 | 位置 |
|------|------|------|
| `thinking()` | LLM 推理，生成思考内容 | base_agent.py:668 |
| `review()` | 审核 LLM 输出是否合法 | base_agent.py:721 |
| `act()` | 解析并执行 Action | base_agent.py:725 |
| `verify()` | 验证执行结果是否正确 | base_agent.py:777 |
| `write_memories()` | 保存执行记忆 | role.py:264 |

## 4. 规划-执行模式 (Plan-Execute)

### 4.1 AutoPlanChatManager

AutoPlanChatManager 是团队管理 Agent，负责协调整个规划执行流程。

**文件位置**: `agent/core/plan/team_auto_plan.py`

**核心逻辑** (`act` 方法):

```python
async def act(self, message, sender, ...) -> ActionOutput:
    for i in range(self.max_round):
        plans = self.memory.plans_memory.get_by_conv_id(conv_id)

        if not plans:  # 如果没有计划，先生成计划
            # 调用 PlannerAgent 生成计划
            planner = await PlannerAgent()
            plan_message = await planner.generate_reply(...)
        else:
            # 获取 TODO 状态的计划
            todo_plans = [p for p in plans if p.state == Status.TODO]

            if not todo_plans:  # 所有计划执行完成
                return ActionOutput(is_exe_success=True, content=final_message)

            # 执行下一个计划
            now_plan = todo_plans[0]
            speaker = await self.select_speaker(...)

            # 处理依赖消息
            rely_prompt, rely_messages = await self.process_rely_message(...)

            # 发送任务给选定的 Agent
            await speaker.generate_reply(current_goal_message, ...)

            # 更新计划状态
            if is_success:
                self.memory.plans_memory.complete_task(conv_id, task_num, result)
```

### 4.2 PlannerAgent

PlannerAgent 负责将复杂任务分解为多个子任务。

**文件位置**: `agent/core/plan/planner_agent.py`

**Profile 配置**:

```python
profile: ProfileConfig = ProfileConfig(
    goal="理解每个智能体及其能力，通过协调智能体协作完成复杂任务",
    constraints=[
        "每个步骤都应推进用户目标",
        "关注任务计划的依赖关系和逻辑",
        "每个步骤是独立可完成的目标",
        "尽量合并有顺序依赖的连续相同步骤",
    ]
)
```

### 4.3 计划数据结构 (GptsPlan)

**文件位置**: `agent/core/memory/gpts/base.py`

```python
@dataclass
class GptsPlan:
    conv_id: str              # 对话 ID
    sub_task_num: int        # 子任务编号
    sub_task_content: str    # 任务内容
    sub_task_title: str      # 任务标题
    sub_task_agent: str      # 负责的 Agent
    rely: str                # 依赖的任务编号 (如 "1,2")
    state: str               # 状态: TODO | COMPLETE | FAILED | RETRYING
    retry_times: int         # 重试次数
    result: str              # 执行结果
```

### 4.4 规划-执行流程图

```
用户请求
    │
    ▼
AutoPlanChatManager.act()
    │
    ├─► 无计划?
    │        │
    │        ▼
    │   PlannerAgent.generate_reply()
    │        │
    │        ▼
    │   PlanAction.run() ──► 分解任务为 GptsPlan 列表
    │
    ├─► 有计划
    │        │
    │        ▼
    │   选择下一个 TODO 计划
    │        │
    │        ▼
    │   select_speaker() ──► 选择负责 Agent
    │        │
    │        ▼
    │   speaker.generate_reply()
    │        │
    │        ▼
    │   ConversableAgent.generate_reply() 循环:
    │        │
    │        ├─► 1. thinking() - LLM 推理
    │        ├─► 2. review() - 审核
    │        ├─► 3. act() - 执行 Action
    │        ├─► 4. verify() - 验证
    │        └─► 5. 失败? ──► 重试/自优化
    │                │
    │                ▼
    │        更新 plans_memory 状态
    │                │
    ▼                ▼
所有计划完成 ◄─────┘
    │
    ▼
返回最终结果
```

## 5. ReAct 模式实现

### 5.1 ReActAgent

ReActAgent 实现了 Thought → Phase → Action → Observation 的推理-行动循环。

**文件位置**: `agent/expand/react_agent.py`

**系统提示模板**:

```
Thought: ...(任务分析和推理)
Phase: ...(当前步骤描述)
Action: ...(选择工具)
Action Input: ...(工具输入)
Observation: ...(执行结果，由系统提供)
```

### 5.2 ReActOutputParser

**文件位置**: `agent/util/react_parser.py`

```python
@dataclass
class ReActStep:
    thought: Optional[str]       # 思考过程
    phase: Optional[str]        # 当前阶段
    action: Optional[str]       # 动作名称
    action_input: Optional[Any] # 动作输入
    observation: Optional[Any]  # 观察结果
    is_terminal: bool          # 是否终止
```

### 5.3 Terminate 动作

Terminate 是特殊的终止动作，当任务完成或无法完成时触发。

```python
class Terminate(Action[None], BaseTool):
    async def run(self, ai_message, ...) -> ActionOutput:
        return ActionOutput(
            is_exe_success=True,
            terminate=True,  # 终止循环
            content=ai_message,
        )
```

## 6. 记忆系统

### 6.1 GptsMemory

混合记忆系统，包含计划记忆和消息记忆。

**文件位置**: `agent/core/memory/gpts/gpts_memory.py`

```python
class GptsMemory:
    _plans_memory: GptsPlansMemory    # 计划记忆
    _message_memory: GptsMessageMemory  # 消息记忆
```

### 6.2 AgentMemory

单 Agent 记忆，支持多种记忆策略。

**文件位置**: `agent/core/memory/agent_memory.py`

- **短期记忆**: 最近对话
- **长期记忆**: 持久化存储
- **重要性评分**: LLMImportanceScorer
- **洞察提取**: LLMInsightExtractor

### 6.3 记忆读写

```python
# 写入记忆 (role.py:264)
async def write_memories(self, question, ai_message, action_output, ...):
    memory_map = {
        "thought": ...,
        "action": ...,
        "observation": ...,
    }
    fragment = StructuredAgentMemoryFragment(memory_map)
    await self.memory.write(fragment)

# 读取记忆 (role.py:255)
async def read_memories(self, question):
    memories = await self.memory.read(question)
    return "".join([m.raw_observation for m in memories])
```

## 7. 关键数据结构

### 7.1 AgentMessage

```python
@dataclass
class AgentMessage:
    content: Optional[str]              # 消息内容
    current_goal: Optional[str]         # 当前目标
    action_report: Optional[ActionOutput]  # Action 输出
    review_info: Optional[AgentReviewInfo] # 审核信息
    resource_info: Optional[Dict]       # 资源信息
```

### 7.2 ActionOutput

```python
class ActionOutput(BaseModel):
    content: str                    # 执行结果
    is_exe_success: bool = True    # 是否成功
    view: Optional[str]             # 可视化内容
    action: Optional[str]           # 动作名
    thoughts: Optional[str]         # 思考过程
    phase: Optional[str]            # 阶段
    observations: Optional[str]     # 观察结果
    next_speakers: Optional[List[str]]  # 下个发言者
    terminate: Optional[bool]       # 是否终止
    have_retry: Optional[bool]      # 是否可重试
```

## 8. 设计模式总结

| 模式 | 应用场景 |
|------|----------|
| **Template Method** | `generate_reply` 定义执行骨架，子类实现具体步骤 |
| **Strategy** | 不同 Agent 类型（ReAct、Planner）实现不同执行策略 |
| **Observer** | 记忆系统作为观察者存储对话历史 |
| **Chain of Responsibility** | Action 链式执行 |
| **DAG 执行** | AWEL 布局使用 DAG 编排多 Agent 协作 |

## 9. AWEL 布局系统

AWEL (Agent Workflow Expression Language) 提供了一种声明式的方式来定义 Agent 协作流程。

**文件位置**: `agent/core/plan/awel/team_awel_layout.py`

### 9.1 AWELBaseManager

```python
class AWELBaseManager(ManagerAgent):
    async def act(self, message, sender, ...) -> ActionOutput:
        # 获取 DAG
        agent_dag = self.get_dag()

        # 构建上下文
        start_context = AgentGenerateContext(...)

        # 执行 DAG
        final_context = await last_node.call(call_data=start_context)
        return final_context
```

### 9.2 布局类型

- **WrappedAWELLayoutManager**: 动态构建 Agent 序列 DAG
- **DefaultAWELLayoutManager**: 使用预定义的 DAG 布局

## 10. 核心文件索引

| 功能 | 文件路径 |
|------|----------|
| Agent 接口定义 | `agent/core/agent.py` |
| ConversableAgent 核心实现 | `agent/core/base_agent.py` |
| 角色定义 | `agent/core/role.py` |
| 规划 Agent | `agent/core/plan/planner_agent.py` |
| 自动规划团队管理 | `agent/core/plan/team_auto_plan.py` |
| PlanAction | `agent/core/plan/plan_action.py` |
| ReActAgent | `agent/expand/react_agent.py` |
| ReActAction | `agent/expand/actions/react_action.py` |
| ReAct 解析器 | `agent/util/react_parser.py` |
| Action 基类 | `agent/core/action/base.py` |
| GptsMemory | `agent/core/memory/gpts/gpts_memory.py` |
| GptsPlan 定义 | `agent/core/memory/gpts/base.py` |
| AWEL 布局管理 | `agent/core/plan/awel/team_awel_layout.py` |
| AgentOperator | `agent/core/plan/awel/agent_operator.py` |
| 团队管理基类 | `agent/core/base_team.py` |

## 11. 配置项

关键配置项（可通过 `DynConfig` 动态配置）:

| 配置项 | 说明 |
|--------|------|
| `dbgpt_agent_plan_team_auto_plan_profile_name` | AutoPlanChatManager 名称 |
| `dbgpt_agent_plan_team_auto_plan_profile_role` | AutoPlanChatManager 角色 |
| `dbgpt_agent_plan_team_auto_plan_profile_goal` | AutoPlanChatManager 目标 |
| `dbgpt_agent_plan_planner_agent_profile_name` | PlannerAgent 名称 |
| `dbgpt_agent_plan_planner_agent_profile_goal` | PlannerAgent 目标 |
| `dbgpt_agent_plan_planner_agent_profile_constraints` | PlannerAgent 约束条件 |