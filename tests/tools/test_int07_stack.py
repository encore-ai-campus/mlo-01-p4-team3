"""`server.tests.int07_stack` 판정 규칙: 필수 컨테이너·지표·화면 상태가 빠지면 확인 완료로 적지 않는다."""
import unittest

from server.tests.int07_stack import GOALS, non_local_endpoints, python_info, required_present, scenario_holds, summarize


def result(**over):
    comp = {"metrics_status": 200, "required": {"m": True}, "python_info": "3.14.6", "process_python": "3.14.6", "alive": True}
    base = {"components": {"web": dict(comp), "ads_web": dict(comp)},
            "containers": [{"service": s, "image": "i", "status": "Up 1 minute (healthy)", "port_matches": True}
                           for s in ("mongo", "kafka", "mysql")],
            "kafka_offsets_before": {"t": 0}, "kafka_offsets_after": {"t": 3}, "http_5xx": 0,
            "games": {**{g: {"reproduced": True} for g in GOALS}, "int1": {"commands": 6, "winner": "village", "errors": []}}}
    base.update(over)
    return base


class Verdicts(unittest.TestCase):
    def test_all_present_is_confirmed(self):
        v = summarize(result())
        self.assertTrue(v["9"][0].startswith("확인 완료"))
        self.assertTrue(v["2"][0].startswith("자동 웹 재현 완료"))

    def test_missing_container_fails(self):
        r = result(containers=[{"service": "mongo", "image": "i", "status": "Up (healthy)"}])
        self.assertEqual(summarize(r)["9"][0], "실패")
        self.assertEqual(summarize(result(containers=[]))["9"][0], "실패")

    def test_other_project_port_mismatch_fails(self):
        r = result()
        r["containers"][0]["port_matches"] = False
        self.assertEqual(summarize(r)["9"][0], "실패")

    def test_unhealthy_or_wrong_python_fails(self):
        r = result()
        r["containers"][1]["status"] = "Up 1 minute (unhealthy)"
        self.assertEqual(summarize(r)["9"][0], "실패")
        r = result()
        r["components"]["web"]["process_python"] = "3.12.1"
        self.assertEqual(summarize(r)["9"][0], "실패")

    def test_remote_endpoint_is_refused(self):
        env = {"PALDO_MONGO_URI": "mongodb://u:p@localhost:27117/paldo_web?replicaSet=rs0", "PALDO_KAFKA_BOOTSTRAP": "localhost:9092",
               "PALDO_ADS_DB_HOST": "127.0.0.1", "PALDO_AUTH_DB_HOST": "127.0.0.1"}
        kc = {"bootstrap.servers": "localhost:9094"}
        self.assertEqual(non_local_endpoints(env, kc), [])
        self.assertEqual(non_local_endpoints(dict(env, PALDO_MONGO_URI="mongodb://u:p@localhost:27117,db.example:27117/x"), kc),
                         ["PALDO_MONGO_URI"])
        self.assertEqual(non_local_endpoints(env, {"bootstrap.servers": "broker.example:9094"}), ["kafka-config bootstrap.servers"])
        self.assertEqual(non_local_endpoints(dict(env, PALDO_ADS_DB_HOST=""), kc), ["PALDO_ADS_DB_HOST"])

    def test_metric_text_parsing(self):
        text = 'python_info{implementation="CPython",major="3",minor="14",patchlevel="6"} 1.0\npaldo_x_total{a="b"} 2.0\n'
        self.assertEqual(python_info(text), "3.14.6")
        self.assertEqual(required_present(text, ["paldo_x", "paldo_y"]), {"paldo_x": True, "paldo_y": False})

    def test_false_reveal_needs_private_murky(self):
        view = {"public": {"claims": [{"subject": "gwabu", "night": 1, "divination_result": "clear"}]},
                "player": {"divinations": {"1": {"gwabu": "murky"}}}}
        self.assertTrue(scenario_holds("false_reveal", view, "gwabu"))
        view["player"]["divinations"]["1"]["gwabu"] = "clear"
        self.assertFalse(scenario_holds("false_reveal", view, "gwabu"))


if __name__ == "__main__":
    unittest.main()
