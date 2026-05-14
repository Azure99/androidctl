package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Test

class OemBackgroundChecklistTest {
    @Test
    fun specUsesManualChecklistCopyAndStableKeys() {
        assertEquals(R.string.section_oem_manual_checklist, oemBackgroundChecklistSpec.titleRes)
        assertEquals(R.string.oem_manual_checklist_intro, oemBackgroundChecklistSpec.introRes)
        assertEquals(R.string.oem_manual_checklist_note, oemBackgroundChecklistSpec.noteRes)
        assertEquals(
            listOf(
                "background_lock",
                "auto_start",
                "associated_launch",
                "power_management",
            ),
            oemBackgroundChecklistSpec.items.map(OemBackgroundChecklistItemSpec::key),
        )
    }

    @Test
    fun specKeepsTheExpectedManualChecklistLabels() {
        assertEquals(
            listOf(
                R.string.oem_checklist_background_lock,
                R.string.oem_checklist_auto_start,
                R.string.oem_checklist_associated_launch,
                R.string.oem_checklist_power_management,
            ),
            oemBackgroundChecklistSpec.items.map(OemBackgroundChecklistItemSpec::labelRes),
        )
    }
}
