"""Checkpointer 多线程并发压测脚本（可重复运行，不依赖真实 LLM）。

背景：`backend/app/graph/builder.py::_create_checkpointer` 原实现为「单个 psycopg
长连接 + PostgresSaver(conn)」，被作为进程级全局单例在 FastAPI 线程池（同步 def
路由）中并发复用。本脚本用于：
  1) 修复前：复现单连接实现的多线程并发行为（异常 / 串行化耗时 / 串话）；
  2) 修复后：同一脚本、同一并发度回归，对比成功率、耗时与数据隔离性。

用法（在 backend/ 目录下执行，需可用的 PostgreSQL）：
    python scripts/checkpointer_concurrency.py --impl current --mode both --threads 30 --ops 20
    python scripts/checkpointer_concurrency.py --impl single  --mode saver --threads 30 --ops 20
    python scripts/checkpointer_concurrency.py --impl pool    --mode saver --threads 30 --ops 20

--impl:
    current  生产实现 app.graph.builder._create_checkpointer()（修复前=单连接，修复后=连接池）
    single   强制复刻「单 psycopg 长连接」实现（修复前基线对照，不受代码改动影响）
    pool     强制使用 psycopg_pool.ConnectionPool（修复后目标形态对照）
--mode:
    saver    直接对 checkpointer 并发 put / get_tuple（每个线程独立 thread_id）
    graph    经 graph.invoke() 端到端并发（fake LLM + interrupt/resume，session_id 留空跳过业务落库）
    both     依次执行以上两者
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── 结果收集 ─────────────────────────────────────────────────
class Result:
    def __init__(self, name: str) -> None:
        self.name = name
        self.thread_ok = 0
        self.thread_fail = 0
        self.ops_per_thread = 0
        self.unit_label = "轮次（每轮 = 1 次 put + 1 次 get_tuple）"
        self.errors: Counter = Counter()
        self.tracebacks: list[str] = []
        self.thread_ids: list[str] = []
        self.mismatches: list[str] = []
        self.thread_elapsed: list[float] = []
        self.wall = 0.0

    @property
    def ops_planned(self) -> int:
        return len(self.thread_ids) * self.ops_per_thread

    @property
    def ops_done(self) -> int:
        return self.thread_ok * self.ops_per_thread

    def record_error(self, exc: BaseException) -> None:
        self.thread_fail += 1
        key = f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
        self.errors[key[:300]] += 1
        if len(self.tracebacks) < 5:
            self.tracebacks.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def report(self) -> None:
        threads = len(self.thread_ids)
        print(f"\n===== [{self.name}] 结果 =====")
        print(f"  并发线程数        : {threads}")
        print(f"  线程成功/失败     : {self.thread_ok} / {self.thread_fail}")
        print(f"  线程成功率        : {(self.thread_ok / threads * 100 if threads else 0):.1f}%")
        print(f"  完成数            : {self.ops_done} / {self.ops_planned}（{self.unit_label}）")
        print(f"  总耗时(wall)      : {self.wall:.3f}s")
        if self.wall > 0 and self.ops_done:
            print(f"  吞吐              : {self.ops_done * 2 / self.wall:.1f} 次调用/s")
        if self.thread_elapsed:
            print(
                "  单线程轮次耗时    : "
                f"min={min(self.thread_elapsed):.3f}s "
                f"median={statistics.median(self.thread_elapsed):.3f}s "
                f"max={max(self.thread_elapsed):.3f}s"
            )
        if self.errors:
            print("  异常分布（类型: 消息 -> 次数）:")
            for key, n in self.errors.most_common():
                print(f"    [{n}x] {key}")
            for i, tb in enumerate(self.tracebacks, 1):
                print(f"  ── 异常完整堆栈样本 #{i} ──")
                print(tb.rstrip())
        if self.mismatches:
            print(f"  数据串话/不一致    : {len(self.mismatches)} 例")
            for m in self.mismatches[:10]:
                print(f"    - {m}")
        else:
            print("  数据串话/不一致    : 0 例（各 thread_id 状态相互独立）")


# ── checkpointer 构造（三种实现） ─────────────────────────────
def make_checkpointer(impl: str, pool_max_size: int | None, pool_min_size: int | None):
    """返回 (checkpointer, 描述, 关闭函数)。"""
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.config import get_settings

    settings = get_settings()
    dsn = settings.checkpointer_dsn

    if impl == "current":
        import app.graph.builder as builder_mod

        cp = builder_mod._create_checkpointer()
        return cp, "生产实现 builder._create_checkpointer()", lambda: close_impl(cp, builder_mod)

    if impl == "single":
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        cp = PostgresSaver(conn)
        cp.setup()
        return cp, "单个 psycopg 长连接 + PostgresSaver(conn)（修复前实现）", lambda: conn.close()

    if impl == "pool":
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=dsn,
            min_size=pool_min_size if pool_min_size is not None else 1,
            max_size=pool_max_size if pool_max_size is not None else 20,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        cp = PostgresSaver(pool)
        cp.setup()
        desc = (
            f"psycopg_pool.ConnectionPool(min_size={pool.min_size}, max_size={pool.max_size}) "
            "+ PostgresSaver(pool)"
        )
        return cp, desc, lambda: pool.close()

    raise ValueError(f"unknown impl: {impl}")


def close_impl(cp, builder_mod):
    """调用生产代码暴露的释放函数（若存在），否则回退到直接关闭底层资源。"""
    close_fn = getattr(builder_mod, "close_interview_graph", None) or getattr(
        builder_mod, "teardown_pool", None
    )
    if close_fn is not None:
        close_fn()
        return
    cp.conn.close()


# ── 模式一：直接并发 put / get_tuple ─────────────────────────
def _saver_worker(cp, index: int, ops: int, barrier: threading.Barrier, result: Result) -> None:
    tid = f"stress-saver-{uuid.uuid4()}"
    token = f"thread-{index}-{uuid.uuid4().hex[:8]}"
    result.thread_ids.append(tid)
    try:
        barrier.wait(timeout=60)
        t0 = time.perf_counter()
        for op in range(ops):
            want = {"token": token, "op": op}
            cid = str(uuid.uuid4())
            checkpoint = {
                "v": 1,
                "id": cid,
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel_values": {"probe": want},
                "channel_versions": {"probe": op + 1},
                "versions_seen": {},
                "pending_sends": [],
            }
            config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
            cp.put(config, checkpoint, {"source": "loop", "step": op, "parents": {}}, {"probe": op + 1})
            # 显式按 checkpoint_id 读回（uuid4 非时间序，不能依赖“最新”语义）
            read_config = {
                "configurable": {"thread_id": tid, "checkpoint_ns": "", "checkpoint_id": cid}
            }
            got = cp.get_tuple(read_config)
            if got is None:
                result.mismatches.append(f"{tid}: get_tuple 返回 None（应为第 {op} 次写入）")
            else:
                got_tid = got.config["configurable"].get("thread_id")
                got_val = got.checkpoint["channel_values"].get("probe")
                got_id = got.checkpoint.get("id")
                if got_tid != tid:
                    result.mismatches.append(f"{tid}: 读回 thread_id={got_tid}（串话）")
                elif got_id != cid:
                    result.mismatches.append(f"{tid}: 读回 checkpoint_id={got_id}，期望 {cid}")
                elif got_val != want:
                    result.mismatches.append(f"{tid}: 读回 channel_values={got_val}，期望 {want}（数据被覆盖/串话）")
        result.thread_elapsed.append(time.perf_counter() - t0)
        result.thread_ok += 1
    except BaseException as exc:  # noqa: BLE001 - 压测需捕获全部异常类型
        result.record_error(exc)


def run_saver_stress(cp, threads: int, ops: int, name: str) -> Result:
    result = Result(name)
    result.ops_per_thread = ops
    barrier = threading.Barrier(threads)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(_saver_worker, cp, i, ops, barrier, result) for i in range(threads)]
        for f in futures:
            f.result()
    result.wall = time.perf_counter() - t0
    return result


def run_single_thread_calibration(cp, ops: int) -> float:
    """单线程校准：返回单次 put+get_tuple 的平均耗时（用于计算串行等效耗时）。"""
    tid = f"stress-calib-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
    t0 = time.perf_counter()
    for op in range(ops):
        cid = str(uuid.uuid4())
        cp.put(
            config,
            {
                "v": 1,
                "id": cid,
                "ts": datetime.now(timezone.utc).isoformat(),
                "channel_values": {"probe": {"token": "calib", "op": op}},
                "channel_versions": {"probe": op + 1},
                "versions_seen": {},
                "pending_sends": [],
            },
            {"source": "loop", "step": op, "parents": {}},
            {"probe": op + 1},
        )
        cp.get_tuple(config)
    elapsed = time.perf_counter() - t0
    try:
        cp.delete_thread(tid)
    except Exception:  # noqa: BLE001 - 清理失败不影响压测结论
        pass
    return elapsed / ops


# ── 模式二：graph.invoke 端到端并发（fake LLM，无真实 LLM） ────
def _install_fake_llm() -> None:
    import importlib
    import json as _json

    from langchain_core.runnables import RunnableLambda

    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")

    def _invoke(_prompt):
        payload = {
            "scores": {"tech_depth": 7, "experience": 7, "communication": 7, "job_fit": 7},
            "comment": "fake-llm 压测评分",
            "follow_up_needed": False,
            "follow_up_question": "",
        }
        return SimpleNamespace(content=_json.dumps(payload, ensure_ascii=False))

    evaluate_mod.get_llm = lambda *a, **kw: RunnableLambda(_invoke)  # type: ignore[assignment]


def _initial_state(thread_id: str, marker: str) -> dict:
    return {
        "thread_id": thread_id,
        "session_id": "",  # 留空 -> 跳过业务落库
        "candidate": {"name": marker, "resume_text": "resume", "job_desc": "jd"},
        "questions": [{"id": "q1", "text": "问题一"}, {"id": "q2", "text": "问题二"}],
        "rubric": [
            {"key": "tech_depth", "name": "技术深度", "weight": 0.40, "max_score": 10},
            {"key": "experience", "name": "项目经验", "weight": 0.30, "max_score": 10},
            {"key": "communication", "name": "沟通表达", "weight": 0.15, "max_score": 10},
            {"key": "job_fit", "name": "岗位匹配", "weight": 0.15, "max_score": 10},
        ],
        "current_idx": 0,
        "max_follow_ups": 2,
        "follow_up_count": 0,
        "messages": [],
        "evaluations": [],
        "status": "pending",
        "report": {},
    }


def _interrupt_value(graph, config):
    snapshot = graph.get_state(config)
    for task in getattr(snapshot, "tasks", None) or []:
        for itr in getattr(task, "interrupts", None) or []:
            return getattr(itr, "value", itr)
    return None


def _graph_worker(graph, index: int, barrier: threading.Barrier, result: Result) -> None:
    from langgraph.types import Command

    tid = f"stress-graph-{uuid.uuid4()}"
    marker = f"candidate-{index}-{uuid.uuid4().hex[:6]}"
    answer_a = f"回答A-{index}"
    answer_b = f"回答B-{index}"
    result.thread_ids.append(tid)
    try:
        barrier.wait(timeout=60)
        t0 = time.perf_counter()
        config = {"configurable": {"thread_id": tid}}
        graph.invoke(_initial_state(tid, marker), config)

        q1 = _interrupt_value(graph, config)
        if not q1 or q1.get("question") != "问题一":
            result.mismatches.append(f"{tid}: 首问异常 {q1}")
        graph.invoke(Command(resume=answer_a), config)
        q2 = _interrupt_value(graph, config)
        if not q2 or q2.get("question") != "问题二":
            result.mismatches.append(f"{tid}: 第二问异常 {q2}")
        graph.invoke(Command(resume=answer_b), config)
        if _interrupt_value(graph, config) is not None:
            result.mismatches.append(f"{tid}: 收尾后仍有 interrupt")

        values = graph.get_state(config).values or {}
        if values.get("thread_id") != tid:
            result.mismatches.append(f"{tid}: state.thread_id={values.get('thread_id')}（串话）")
        if (values.get("candidate") or {}).get("name") != marker:
            result.mismatches.append(f"{tid}: candidate={values.get('candidate')}（串话）")
        if values.get("status") != "finished":
            result.mismatches.append(f"{tid}: status={values.get('status')}，期望 finished")
        evals = values.get("evaluations") or []
        if len(evals) != 2:
            result.mismatches.append(f"{tid}: evaluations={len(evals)}，期望 2（可能被并发覆盖）")
        blob = json.dumps(values.get("messages", []), ensure_ascii=False, default=str)
        if answer_a not in blob or answer_b not in blob:
            result.mismatches.append(f"{tid}: messages 缺少本线程回答（串话/丢失）")
        for other in (index + 1, index + 2):
            if f"回答A-{other}" in blob or f"回答B-{other}" in blob:
                result.mismatches.append(f"{tid}: messages 混入其他线程回答 {other}（串话）")

        result.thread_elapsed.append(time.perf_counter() - t0)
        result.thread_ok += 1
    except BaseException as exc:  # noqa: BLE001
        result.record_error(exc)


def run_graph_stress(graph, threads: int, name: str) -> Result:
    result = Result(name)
    result.ops_per_thread = 1
    result.unit_label = "线程（每线程一轮完整 interview：invoke→resume→resume）"
    barrier = threading.Barrier(threads)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(_graph_worker, graph, i, barrier, result) for i in range(threads)]
        for f in futures:
            f.result()
    result.wall = time.perf_counter() - t0
    return result


def _probe_once(cp, tag: str) -> None:
    """一次 put + get_tuple 往返，并校验读回内容一致。"""
    tid = f"stress-recovery-{tag}-{uuid.uuid4()}"
    want = {"tag": tag, "ts": time.time()}
    cid = str(uuid.uuid4())
    config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
    cp.put(
        config,
        {
            "v": 1,
            "id": cid,
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel_values": {"probe": want},
            "channel_versions": {"probe": 1},
            "versions_seen": {},
            "pending_sends": [],
        },
        {"source": "loop", "step": 0, "parents": {}},
        {"probe": 1},
    )
    got = cp.get_tuple(
        {"configurable": {"thread_id": tid, "checkpoint_ns": "", "checkpoint_id": cid}}
    )
    if got is None or got.checkpoint["channel_values"].get("probe") != want:
        raise AssertionError(f"读回内容不一致: {got}")


def _snapshot_pids(admin_conn) -> set[int]:
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT pid FROM pg_stat_activity "
            "WHERE usename = current_user AND datname = current_database() "
            "AND pid <> pg_backend_pid()"
        )
        return {row[0] for row in cur.fetchall()}


def run_recovery_probe(cp, pids_before: set[int], probes: int = 5) -> bool:
    """模拟数据库端连接中断（PG 重启 / kill / 网络抖动），验证 saver 能否自愈。

    返回 True 表示「中断后全部探针成功」（完全自愈）。
    """
    import psycopg

    from app.config import get_settings

    admin = psycopg.connect(get_settings().checkpointer_dsn, autocommit=True)
    print("\n===== [recovery] 连接中断后的自愈能力 =====")
    try:
        _probe_once(cp, "warmup")
        targets = sorted(_snapshot_pids(admin) - pids_before)
        print(f"  该实现当前持有的后端连接 pid: {targets or '（未识别到新增连接）'}")
        if not targets:
            print("  !! 未识别到新增后端连接，无法进行中断测试")
            return False
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM unnest(%s::int[]) AS t(pid)",
                (targets,),
            )
            killed = sum(1 for row in cur.fetchall() if row[0])
        print(f"  已强制终止后端连接: {killed}/{len(targets)}（模拟极大化故障）")
        time.sleep(0.3)

        ok = fails = 0
        for i in range(probes):
            try:
                _probe_once(cp, f"probe{i}")
                ok += 1
                print(f"  探针 {i + 1}/{probes}: 成功")
            except BaseException as exc:  # noqa: BLE001 - 需记录故障类型
                fails += 1
                print(
                    f"  探针 {i + 1}/{probes}: 失败 -> "
                    f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:160]}"
                )
        print(f"  结论: 连接被中断后，{probes} 次探针 成功 {ok} / 失败 {fails}")
        current_pids = sorted(_snapshot_pids(admin))
        residual = sorted(set(current_pids) & set(targets))
        print(
            f"  当前后端连接 pid: {current_pids}（被终止的 {targets} 中仍存活: "
            f"{residual or '无'}；若列表中出现新 pid 则说明已重建连接）"
        )
        if fails == probes:
            print("  → 无法自愈：连接一旦失效，后续所有请求持续失败（需重启进程）")
        elif ok == probes:
            print("  → 完全自愈：连接失效后无感知恢复，业务不中断")
        else:
            print("  → 部分自愈：坏连接被丢弃后自动重建（仅首次检出时失败）")
        return ok == probes
    finally:
        admin.close()


def cleanup_stress_rows() -> int:
    """清理本脚本产生的压测数据（thread_id 前缀 stress-），保持可重复运行。"""
    import psycopg

    from app.config import get_settings

    with psycopg.connect(get_settings().checkpointer_dsn, autocommit=True) as conn:
        total = 0
        with conn.cursor() as cur:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cur.execute(f"DELETE FROM {table} WHERE thread_id LIKE 'stress-%'")
                total += cur.rowcount
        return total


# ── 入口 ─────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Checkpointer 并发压测")
    parser.add_argument("--impl", choices=["current", "single", "pool"], default="current")
    parser.add_argument("--mode", choices=["saver", "graph", "both"], default="saver")
    parser.add_argument("--threads", type=int, default=30)
    parser.add_argument("--ops", type=int, default=20, help="saver 模式每个线程的 put+get 轮数")
    parser.add_argument("--pool-max-size", type=int, default=None)
    parser.add_argument("--pool-min-size", type=int, default=None)
    parser.add_argument(
        "--with-recovery",
        action="store_true",
        help="额外执行「连接中断自愈」探针（强制 kill 后端连接后连续探针）",
    )
    args = parser.parse_args()

    import logging

    # psycopg_pool 在池未显式 open 时打印 INFO 级提示，这里保持输出可读
    logging.getLogger("psycopg.pool").setLevel(logging.WARNING)

    import psycopg
    from psycopg_pool import ConnectionPool

    from app.config import get_settings

    dsn = get_settings().checkpointer_dsn
    safe_dsn = dsn.replace(get_settings().database_url.split("@")[0].split("//")[-1], "***")
    print("================ Checkpointer 并发压测 ================")
    print(f"  DSN        : {safe_dsn}")
    print(f"  impl       : {args.impl}")
    print(f"  mode       : {args.mode}" + (" + recovery" if args.with_recovery else ""))
    print(f"  threads    : {args.threads}")
    if args.mode in {"saver", "both"}:
        print(f"  ops/thread : {args.ops}（每轮 1 次 put + 1 次 get_tuple）")
    print(f"  python     : {sys.version.split()[0]}")

    pids_before: set[int] = set()
    if args.with_recovery:
        admin = psycopg.connect(dsn, autocommit=True)
        try:
            pids_before = _snapshot_pids(admin)
        finally:
            admin.close()

    cp, desc, close_fn = make_checkpointer(args.impl, args.pool_max_size, args.pool_min_size)
    print(f"  实现描述   : {desc}")

    if isinstance(getattr(cp, "conn", None), ConnectionPool):
        print(
            "  池配置     : "
            f"min_size={cp.conn.min_size} max_size={cp.conn.max_size} "
            f"kwargs={cp.conn.kwargs}"
        )
    elif isinstance(getattr(cp, "conn", None), psycopg.Connection):
        print(f"  连接对象   : {type(cp.conn).__name__}（单连接，无池）")

    exit_code = 0
    try:
        if args.mode in {"saver", "both"}:
            per_op = run_single_thread_calibration(cp, max(args.ops, 10))
            print(f"\n[校准] 单线程单次 put+get_tuple 平均耗时: {per_op * 1000:.3f} ms")
            r = run_saver_stress(cp, args.threads, args.ops, "saver-put/get_tuple")
            r.report()
            serial_equivalent = per_op * r.ops_planned
            print(
                f"  [对比] 若完全串行执行，理论耗时≈{serial_equivalent:.3f}s；"
                f"实测 {r.wall:.3f}s，并发加速比≈{(serial_equivalent / r.wall if r.wall else 0):.2f}x"
            )
            if r.thread_fail or r.mismatches:
                exit_code = 1

        if args.mode in {"graph", "both"}:
            _install_fake_llm()
            from app.graph.builder import build_interview_graph

            graph = build_interview_graph(checkpointer=cp)
            r = run_graph_stress(graph, args.threads, "graph.invoke 端到端")
            r.report()
            if r.thread_fail or r.mismatches:
                exit_code = 1

        if args.with_recovery:
            if not run_recovery_probe(cp, pids_before):
                exit_code = 1
    finally:
        print("\n[清理] 关闭 checkpointer 资源 ...")
        try:
            close_fn()
            print("[清理] 完成")
        except BaseException as exc:  # noqa: BLE001
            print(f"[清理] 失败: {type(exc).__name__}: {exc}")
            exit_code = 1
        try:
            print(f"[清理] 删除压测数据 {cleanup_stress_rows()} 行")
        except BaseException as exc:  # noqa: BLE001
            print(f"[清理] 压测数据清理失败: {type(exc).__name__}: {exc}")

    print("\n================ 压测结束 ================")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())