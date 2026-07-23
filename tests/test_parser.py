import unittest

from ui_discovery.parser import parse_hierarchy, summarize


SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="example" content-desc="" checkable="false" checked="false"
        clickable="false" enabled="true" focusable="false" focused="false"
        scrollable="false" long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,1920]" displayed="true">
    <node index="0" text="Continue" resource-id="example:id/continue"
          class="android.widget.Button" package="example" content-desc="Continue setup"
          checkable="false" checked="false" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[100,200][500,300]" displayed="true"/>
  </node>
</hierarchy>"""


class ParserTests(unittest.TestCase):
    def test_extracts_clickable_element_and_geometry(self):
        records = parse_hierarchy(SAMPLE)
        button = records[2]
        self.assertEqual(button["text"], "Continue")
        self.assertEqual(button["interaction"], "click")
        self.assertEqual(button["rect"]["center_x"], 300)
        self.assertEqual(button["rect"]["height"], 100)
        self.assertEqual(
            button["xpath"],
            "/hierarchy[1]/android.widget.FrameLayout[1]/android.widget.Button[1]",
        )

    def test_summarizes_hierarchy(self):
        result = summarize(parse_hierarchy(SAMPLE))
        self.assertEqual(result["total_elements"], 3)
        self.assertEqual(result["clickable_elements"], 1)
        self.assertEqual(result["resource_id_elements"], 1)


if __name__ == "__main__":
    unittest.main()
