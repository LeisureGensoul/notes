# Reverse debugging

**反向调试**: 恢复并检查过去的执行状态, 从异常结果追溯产生它的操作.

> 流程: **观察异常 → 找到相关状态变化 → 返回变化前 → 检查原因**.

## 记录与恢复

| 对象 | 职责 |
| --- | --- |
| Breakpoint | 在指定位置或条件处暂停 |
| Watchpoint | 在被观察的数据变化或发生指定访问时暂停 |
| Checkpoint | 保存可恢复的执行状态, 缩短后续恢复距离 |
| 执行记录 | 保存恢复所需的信息, 如状态变化或非确定性事件 |

**记录内容决定恢复方式**:

| 实现 | 记录与恢复方式 |
| --- | --- |
| [GDB record full](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Process-Record-and-Replay.html) | 记录指令执行造成的状态变化; 回放时根据日志恢复寄存器与内存 |
| GDB record btrace | 记录硬件分支轨迹; 可回溯控制流, 不记录寄存器与内存的历史值 |
| [rr](https://rr-project.org/) | 记录 Linux 用户态执行中的非确定性输入与事件; 恢复 checkpoint 后向前重演到目标位置 |

rr 的事件记录约束重演中的输入与执行顺序, checkpoint 保存可恢复状态. [ReplayTimeline](https://github.com/rr-debugger/rr/blob/master/src/ReplayTimeline.h) 管理 checkpoint, 并协调恢复与重演.

以 rr 为例, C0 与 C1 是 checkpoint, E 是异常位置, t 是回溯目标:

```text
时间: C0 ───── C1 ─── t ───── E
回溯: E → 恢复 C1 → 正向重演至 t
```

checkpoint 的间隔影响重演距离, 保存与恢复本身也有成本.

[UDB](https://undo.io/resources/reverse-debugging-whitepaper/) 同样利用记录与回放支持反向调试. 回放开销取决于记录机制与负载.

## 排错流程

1. **缩小范围**: 选定异常前的正常位置与最早可观察到异常的位置.
2. **记录执行**: GDB 运行到正常位置后启用 `record full`. rr 使用 `rr record` 记录复现过程, 再用 `rr replay` 进入调试.
3. **观察异常**: 运行或回放到异常位置, 检查变量, 调用栈与相关内存, 设置数据 watchpoint.
4. **追溯变化**: 反向找到上一次相关变化, 检查变化前的数据与控制流.

[GDB 反向命令](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Reverse-Execution.html):

| 命令 | 作用 |
| --- | --- |
| `reverse-continue` | 向过去执行, 直到断点或 watchpoint 触发 |
| `reverse-step` | 回到上一个执行过的源代码行, 可进入函数 |
| `reverse-next` | 按源代码行回溯, 跨过函数调用 |
| `reverse-stepi` | 回退一条机器指令 |

rr 的[反向 watchpoint 示例](https://github.com/rr-debugger/rr/wiki/Usage#reverse-execution) 使用 `watch -l` 固定观察位置. 反向停止时显示的值变化方向也随之反转, 应结合写入语句理解前后状态.

GDB record 的回溯范围受保留日志限制; rr 可重复重演同一次记录.

## 对调试方式的思考

静态分析检查代码约束, 日志预选运行信息, 断点与 watchpoint 定位关注的状态. **定位问题需要的是相关状态与执行区间**.

我更关注两个方向:

- **缩短恢复距离**: 在反复访问的区间保留 checkpoint, 平衡保存成本, 内存占用与重演时间.
- **减少人工定位**: 根据错误条件与数据依赖筛选检查位置, 让反向搜索直接服务于原因分析.
