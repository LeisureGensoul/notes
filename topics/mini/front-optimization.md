# CPU 前端优化

CPU 前端负责取指, 预测控制流并向后端供给指令. 优化目标是减少热路径上的取指等待与无效工作, 提高实际吞吐或降低延迟.

> 路线: **定位供给瓶颈 → 调整代码布局与工作集 → 在相同负载下验证收益**.

## 瓶颈与指标

[Intel TMA](https://www.intel.com/content/www/us/en/docs/vtune-profiler/cookbook/2024-0/top-down-microarchitecture-analysis-method.html) 按 pipeline slots 分析 Retiring, Bad Speculation, Front-End Bound 和 Back-End Bound. **Front-End Bound 表示后端可接收时, 因前端供给不足而空置的槽位占比**, 不能直接当作停顿周期比例.

| 原因 | 关注内容 |
| --- | --- |
| 取指延迟 | 指令缓存未命中, iTLB 地址翻译, 分支重定向 |
| 供给带宽 | 取指与解码能力, 微操作缓存覆盖 |
| 无效执行 | 分支预测失败后的错误路径与恢复开销 |

TMA 中的 Front-End Latency 与 Front-End Bandwidth 用于进一步区分供给问题; 分支误预测还会计入 Bad Speculation. 各项指标按处理器的事件定义与负载基线解释.

## 优化方法

| 方法 | 作用 | 取舍 |
| --- | --- | --- |
| 热点对齐 | 改善热点入口或循环的取指位置 | 填充会增大代码体积 |
| 基本块重排 | 将高频控制流相邻放置, 增加顺序执行 | 布局依赖分支频率与跳转距离 |
| 冷热拆分 | 将冷块移出热区域, 缩小热路径覆盖的缓存行与代码页 | 需要保持控制流, 异常与展开信息 |
| 函数重排 | 根据调用关系与热度聚集函数 | 频率与函数大小共同影响布局 |
| 大页映射 | 扩大 TLB 条目的地址覆盖范围 | 依赖页大小, 映射方式与运行环境 |

冷热拆分可保留原函数内的跳转关系.

函数重排可由链接器或二进制优化器完成. [BOLT 的 ReorderFunctions](https://github.com/llvm/llvm-project/blob/main/bolt/lib/Passes/ReorderFunctions.cpp) 提供 HFSort, HFSort+ 和 CDSort 等布局策略.

## Profile 与实现路线

运行时信息 (profile) 描述代表性负载下的执行频率与调用关系. [Clang PGO](https://clang.llvm.org/docs/UsersManual.html#profile-guided-optimization) 可利用插桩或采样数据指导内联, 分支与代码布局.

| 路线 | 输入与处理阶段 | 主要作用 |
| --- | --- | --- |
| 插桩 PGO | 插桩程序运行后生成 profile, 再编译 | 提供较详细的执行计数 |
| 采样 PGO / [AutoFDO](https://github.com/google/autofdo) | 将采样信息映射回编译器 | 用运行负载指导编译优化 |
| [BOLT](https://github.com/llvm/llvm-project/blob/main/bolt/README.md) | 根据 profile 重写已链接二进制 | 基本块与函数重排, 冷热拆分 |
| [Propeller](https://github.com/google/llvm-propeller) | 根据 profile 生成布局信息, 由编译器和链接器重新构建 | 利用基本块分节支持更细粒度布局 |

Clang 的 [basic block sections](https://clang.llvm.org/docs/UsersManual.html#cmdoption-fbasic-block-sections) 为链接器提供基本块级布局能力. 训练负载应覆盖实际使用场景.

## 测量

| 指标 | 回答的问题 |
| --- | --- |
| 延迟, 吞吐与 CPU 时间 | 实际工作是否更快 |
| iCache 与 iTLB 事件 | 取指和地址翻译是否存在压力 |
| 分支预测事件 | 控制流预测是否有效 |
| Intel DSB (Decoded Stream Buffer) 覆盖 | 微操作缓存能否提供足够的已解码指令 |
| 热代码字节数与覆盖页数 | 常用代码是否集中 |
| 二进制与代码段大小 | 布局变化是否引入额外体积 |

Linux 可用 perf, Android 可用 [simpleperf](https://android.googlesource.com/platform/system/extras/+/master/simpleperf/doc/README.md). [llvm-bolt-heatmap](https://github.com/llvm/llvm-project/blob/main/bolt/docs/Heatmaps.md) 将采样信息按代码地址聚合, 用于对比布局.

**比较口径**: 固定输入与运行阶段, 记录 CPU 核, PMU 事件和分母. 热代码范围需说明频率阈值与页大小. 前端指标用于解释变化, 最终效果看延迟或吞吐.

## Plus: 示例观测

使用 simpleperf 在 Pixel 7 上采样.

> Stall 按周期占比列示.

| 指标 | 分组 | 今日头条 | 淘宝 | 番茄小说 | 快手 | 哔哩哔哩 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| ICache Miss Rate | Overall | 8.2% | 8.3% | 8.0% | 9.1% | 8.6% |
| ICache Miss Rate | Odex-Part | 10.7% | 12.2% | 11.1% | 10.2% | 13.0% |
| Branch Miss Rate | Overall | 2.6% | 2.7% | 2.7% | 3.0% | 2.8% |
| Branch Miss Rate | Odex-Part | 3.2% | 3.8% | 3.5% | 3.0% | 4.8% |
| Front-End Stall | --- | 35.5% | 35.2% | 33.7% | 35.7% | 35.1% |
| Back-End Stall | --- | 37.4% | 37.3% | 33.6% | 36.9% | 37.8% |
