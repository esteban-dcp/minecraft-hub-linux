"""Clear a GPU safety block where it is met, not three menus away.

PLAY refuses the launch, so the failure dialog is where the acknowledgement
belongs. It used to only tell the player to open Settings ▸ Tools and find
the action there, which is the whole reason an ordinary reboot with the game
still open became a recurring chore. None of the safety decision moves here:
eligibility is still re-checked under the launch lock before anything is
written, and these tests pin that the confirmation is never skipped.
"""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from minecrafthub import gui
from minecrafthub.gpu_safety import GpuSafetyAcknowledgementStatus


def _status(previous_boot_fault=False, message="an interrupted launch"):
    return GpuSafetyAcknowledgementStatus(
        code="previous-boot",
        can_acknowledge=True,
        message=message,
        marker_present=True,
        previous_boot_fault=previous_boot_fault,
    )


class FakeMessageBox:
    """Records what the dialogs were asked, and answers yes or no."""

    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []
        self.info = []
        self.errors = []

    def askyesno(self, title, message, parent=None):
        self.asked.append((title, message, parent))
        return self.answer

    def showinfo(self, title, message, parent=None):
        self.info.append((title, message, parent))

    def showerror(self, title, message, parent=None):
        self.errors.append((title, message, parent))


class AcknowledgementOfferTests(unittest.TestCase):
    def _offer(self, answer=True, acknowledged=True, status=None, **kwargs):
        box = FakeMessageBox(answer)
        ack = mock.Mock(return_value=acknowledged)
        with mock.patch.object(gui, "acknowledge_gpu_crash", ack), \
                mock.patch.object(gui, "gpu_crash_acknowledgement_status",
                                  return_value=_status(
                                      message="still refused")):
            result = gui._offer_gpu_incident_acknowledgement(
                box, "parent", status or _status(), **kwargs)
        return result, box, ack

    def test_declining_the_confirmation_writes_nothing(self):
        result, box, ack = self._offer(answer=False)
        self.assertFalse(result)
        self.assertFalse(ack.called)
        self.assertEqual(box.info, [])

    def test_accepting_acknowledges_once_and_confirms(self):
        result, box, ack = self._offer()
        self.assertTrue(result)
        self.assertEqual(ack.call_count, 1)
        self.assertIn("acknowledged", box.info[0][1])

    def test_a_refused_acknowledgement_reports_why_and_fails(self):
        result, box, ack = self._offer(acknowledged=False)
        self.assertFalse(result)
        self.assertTrue(ack.called)
        self.assertEqual(box.info, [])
        # The live status is what explains the refusal, not the stale one.
        self.assertEqual(box.errors[0][1], "still refused")

    def test_the_block_itself_is_still_shown_in_full(self):
        # The launch path passes the failure text as the prefix; losing it
        # would trade one annoyance for a dialog that explains nothing.
        _, box, _ = self._offer(prefix="Unsafe graphics session: …\n\n",
                                title="Minecraft could not start")
        title, message, parent = box.asked[0]
        self.assertEqual(title, "Minecraft could not start")
        self.assertTrue(message.startswith("Unsafe graphics session: …"))
        self.assertIn("an interrupted launch", message)
        self.assertEqual(parent, "parent")

    def test_the_confirmation_names_what_must_be_checked_first(self):
        _, box, _ = self._offer(status=_status(previous_boot_fault=True))
        self.assertIn("repairing/updating the graphics driver",
                      box.asked[0][1])
        _, box, _ = self._offer(status=_status(previous_boot_fault=False))
        self.assertIn("No fatal driver event was detected", box.asked[0][1])

    def test_the_default_title_is_kept_for_the_settings_entry(self):
        _, box, _ = self._offer()
        self.assertEqual(box.asked[0][0], "Acknowledge previous GPU incident")


class SafetyInstructionTests(unittest.TestCase):
    def test_a_verified_driver_fault_demands_a_repair_and_a_reboot(self):
        self.assertIn("rebooting", gui._gpu_incident_safety_instruction(
            _status(previous_boot_fault=True)))

    def test_an_unexplained_stop_demands_an_inspection(self):
        instruction = gui._gpu_incident_safety_instruction(_status())
        self.assertIn("why the previous session or machine stopped",
                      instruction)


if __name__ == "__main__":
    unittest.main()
