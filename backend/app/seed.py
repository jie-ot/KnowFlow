"""供测试显式加载的示例数据；正式启动不自动写入。"""

from __future__ import annotations

import sqlite3

from .db import dumps, new_id, now_iso
from .logic.timeutil import add_days, today_str

OS_NOTE = r"""> 摘要：进程是资源分配的基本单位，线程是调度的基本单位；同一进程的线程共享地址空间，切换开销更小。

## 进程与线程的区别

| 对比项 | 进程 | 线程 |
| --- | --- | --- |
| 资源 | 拥有独立地址空间 | 共享所属进程的资源 |
| 切换开销 | 大（要切换页表） | 小 |
| 通信 | 管道、消息队列、共享内存 | 直接读写共享变量 |

## 周转时间

周转时间 = 完成时间 − 到达时间，平均周转时间：

$$T = \frac{1}{n}\sum_{i=1}^{n} (C_i - A_i)$$

## 创建子进程

```c
#include <unistd.h>
#include <stdio.h>

int main(void) {
    pid_t pid = fork();
    if (pid == 0) printf("child\n");
    else printf("parent of %d\n", pid);
    return 0;
}
```

`fork()` 调用一次、返回两次：子进程中返回 0，父进程中返回子进程的 PID。

相关笔记：[[进程调度算法]]
"""

SCHED_NOTE = r"""## 常见调度算法

| 算法 | 是否抢占 | 特点 |
| --- | --- | --- |
| FCFS 先来先服务 | 否 | 简单，对短作业不利 |
| SJF 短作业优先 | 可抢占（SRTF） | 平均等待时间最短，需要预知运行时间 |
| RR 时间片轮转 | 是 | 响应快，时间片太小切换开销大 |

调度的对象是 [[进程与线程]] 里讲的线程；比较算法时常用 **平均周转时间**。
"""

MATH_NOTE = r"""## 泰勒公式

若 $f(x)$ 在 $x_0$ 处 $n$ 阶可导，则

$$f(x) = \sum_{k=0}^{n} \frac{f^{(k)}(x_0)}{k!}(x - x_0)^k + o\big((x - x_0)^n\big)$$

## 常用麦克劳林展开

- $e^x = 1 + x + \frac{x^2}{2!} + \cdots + \frac{x^n}{n!} + o(x^n)$
- $\sin x = x - \frac{x^3}{3!} + \frac{x^5}{5!} - \cdots$
- $\ln(1+x) = x - \frac{x^2}{2} + \frac{x^3}{3} - \cdots$
"""


def seed(conn: sqlite3.Connection) -> None:
    now, today = now_iso(), today_str()
    os_id, math_id = new_id(), new_id()
    os_note, math_note, sched_note = new_id(), new_id(), new_id()
    with conn:
        conn.executemany(
            "INSERT INTO courses(id, name, color, sort, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(os_id, "操作系统（示例）", "blue", 1, now, now), (math_id, "高等数学（示例）", "orange", 2, now, now)],
        )
        conn.executemany(
            "INSERT INTO notes(id, course_id, title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(os_note, os_id, "进程与线程", OS_NOTE, dumps(["第 2 章", "期中"]), now, now),
             (sched_note, os_id, "进程调度算法", SCHED_NOTE, dumps(["第 2 章"]), now, now),
             (math_note, math_id, "泰勒公式", MATH_NOTE, dumps(["第 3 章"]), now, now)],
        )
        cards = [
            (os_id, os_note, "进程和线程，谁是资源分配的基本单位？", "进程是资源分配的基本单位；线程是 CPU 调度的基本单位。"),
            (os_id, os_note, "fork() 的返回值", "子进程中返回 0；父进程中返回子进程的 PID；失败返回 −1。"),
            (os_id, os_note, "平均周转时间公式", r"$$T = \frac{1}{n}\sum_{i=1}^{n} (C_i - A_i)$$"),
            (math_id, math_note, "$e^x$ 的麦克劳林展开", r"$e^x = 1 + x + \frac{x^2}{2!} + \cdots + \frac{x^n}{n!} + o(x^n)$"),
        ]
        conn.executemany(
            "INSERT INTO cards(id, course_id, source_note_id, front, back, due, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(new_id(), c, n, f, b, today, now, now) for c, n, f, b in cards],
        )
        plan_id = new_id()
        conn.execute(
            "INSERT INTO plans(id, course_id, goal, progress_text, deadline, daily_minutes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, os_id, "操作系统期中复习（示例）", "前两章看完了，第 3 章同步与互斥还没开始",
             add_days(today, 14), 90, now, now),
        )
        tasks = [
            ("复习进程调度算法", "review", today, 30),
            ("整理第 3 章同步与互斥笔记", "learn", add_days(today, 1), 45),
            ("做往年试卷第 2 套", "practice", add_days(today, 3), 60),
        ]
        conn.executemany(
            "INSERT INTO tasks(id, plan_id, title, kind, due_date, estimated_minutes, sort, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(new_id(), plan_id, t, k, d, m, i, now, now) for i, (t, k, d, m) in enumerate(tasks)],
        )
