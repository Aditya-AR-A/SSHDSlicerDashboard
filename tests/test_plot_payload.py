import unittest

import pandas as pd

from slicing_dashboard.plots.assigned_chart import build_assigned_chart
from slicing_dashboard.plots.tables import create_table


class PlotPayloadTests(unittest.TestCase):
    def test_assigned_segments_preserve_each_users_stacking_and_hover_details(self):
        rows = [
            {"User": "Riya", "ID": "Riya2", "Stage": "Rework Assigned", "Duration": 3600,
             "Count": 4, "DaysAssigned": 2, "AssignedDate": "2026-10-03"},
            {"User": "Riya", "ID": "Riya1", "Stage": "New Assigned", "Duration": 7200,
             "Count": 8, "DaysAssigned": 0, "AssignedDate": "2026-10-05"},
            {"User": "Komal", "ID": "Komal1", "Stage": "New Assigned", "Duration": 1800,
             "Count": 2, "DaysAssigned": 1, "AssignedDate": "2026-10-04"},
            {"User": "Riya", "ID": "Riya3", "Stage": "New Assigned", "Duration": 900,
             "Count": 1, "DaysAssigned": 3, "AssignedDate": "2026-10-02"},
        ]
        figure = build_assigned_chart(pd.DataFrame(rows), True)
        bars = [trace for trace in figure.data if trace.type == "bar"]
        self.assertEqual(len(bars), 3)
        self.assertEqual(list(figure.layout.yaxis.categoryarray), ["Komal", "Riya"])

        segments = {"Komal": [], "Riya": []}
        for trace in bars:
            self.assertEqual(len(set(trace.y)), len(trace.y))
            self.assertFalse(trace.showlegend)
            for index, user in enumerate(trace.y):
                segments[user].append((trace.x[index], trace.customdata[index][3],
                                       trace.customdata[index][1], trace.customdata[index][2],
                                       trace.marker.color[index], trace.textfont.color[index]))

        self.assertEqual(segments["Komal"], [(0.5, "Komal1", "New Assigned", 2, "#4ade80", "#0f172a")])
        self.assertEqual(segments["Riya"], [
            (2.0, "Riya1", "New Assigned", 8, "#86efac", "#0f172a"),
            (0.25, "Riya3", "New Assigned", 1, "#15803d", "#ffffff"),
            (1.0, "Riya2", "Rework Assigned", 4, "#f43f5e", "#ffffff"),
        ])
        self.assertEqual(sum(sum(trace.x) for trace in bars), sum(row["Duration"] for row in rows) / 3600)

    def test_assigned_payload_scales_with_segments_per_person(self):
        frame = pd.DataFrame([
            {"User": f"Person {user}", "ID": f"Account {user}-{segment}",
             "Stage": "New Assigned" if segment < 5 else "Rework Assigned",
             "Duration": 1000 + segment, "Count": segment + 1,
             "AssignedDate": "2026-10-05", "DaysAssigned": segment}
            for user in range(12) for segment in range(10)
        ])
        figure = build_assigned_chart(frame, True)
        bars = [trace for trace in figure.data if trace.type == "bar"]
        self.assertEqual(len(bars), 10)
        self.assertEqual(sum(len(trace.x) for trace in bars), 120)
        # Before batching, this fixture produced 124 traces and 103,238 bytes.
        self.assertLess(len(figure.to_json().encode("utf-8")), 50_000)

    def test_native_pagination_keeps_all_records_and_sorting(self):
        rows = pd.DataFrame({"User": [f"Person {index}" for index in range(80)],
                             "Total Duration": list(range(80))})
        table = create_table(rows, True)
        self.assertEqual(table.page_action, "native")
        self.assertEqual(table.page_size, 25)
        self.assertEqual(table.sort_action, "native")
        self.assertEqual(table.data, rows.to_dict("records"))


if __name__ == "__main__":
    unittest.main()
