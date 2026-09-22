//! Baseline seeding (Task 14): the Rust seed latch. Mirrors the Python
//! `_SeedLatch` fixture in `tests/test_eval_seed.py` for the run-edge fire.

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

fn load(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_with_seed_range(range: Option<(f64, f64)>) -> Protocol {
    let source = std::fs::read_to_string("tests/fixtures/seed_run.ir.json").unwrap();
    let mut json: serde_json::Value = serde_json::from_str(&source).unwrap();
    let control = &mut json["controls"]["thr_uv"];
    match range {
        Some((low, high)) => {
            control["range_low"] = serde_json::json!({"node": "number", "value": low});
            control["range_high"] = serde_json::json!({"node": "number", "value": high});
        }
        None => {
            control.as_object_mut().unwrap().remove("range_low");
            control.as_object_mut().unwrap().remove("range_high");
        }
    }
    serde_json::from_value(json).unwrap()
}

fn run_seed_value(protocol: &Protocol, value: f64) -> Evaluator {
    let mut ev = Evaluator::new(protocol, 256.0, &["Cz".to_string()]);
    ev.start(false);
    for _ in 0..4 {
        ev.step_chunk_events(&vec![vec![value]; 256]);
    }
    ev
}

#[test]
fn fires_once_and_writes_the_measured_percentile() {
    let p = load("seed_run");
    let mut ev = Evaluator::new(&p, 256.0, &["Cz".to_string()]);
    ev.start(false);
    for _ in 0..4 {
        ev.step_chunk_events(&vec![vec![5.0_f64]; 256]);
    } // 3 warmup + 1 run chunk
    let r = ev.seed_report();
    let e = &r["thr_uv"];
    assert_eq!(e.status, "seeded");
    assert!((e.value.unwrap() - 5.0).abs() < 1e-9, "seeded value {:?}", e.value);
    assert!((e.target_pct - 70.0).abs() < 1e-9);
}

#[test]
fn number_seed_is_clamped_to_declared_range() {
    let protocol = load_with_seed_range(Some((0.5, 1.0)));
    for (measured, expected) in [(0.25, 0.5), (0.75, 0.75), (1.25, 1.0)] {
        let ev = run_seed_value(&protocol, measured);
        assert_eq!(ev.seed_report()["thr_uv"].value, Some(expected));
        let applied = ev.last_streams()["thr"].last().copied().unwrap();
        assert_eq!(applied, expected);
    }
}

#[test]
fn number_seed_without_range_preserves_measured_value() {
    let protocol = load_with_seed_range(None);
    let ev = run_seed_value(&protocol, 1.25);
    assert_eq!(ev.seed_report()["thr_uv"].value, Some(1.25));
    assert_eq!(ev.last_streams()["thr"].last().copied(), Some(1.25));
}
