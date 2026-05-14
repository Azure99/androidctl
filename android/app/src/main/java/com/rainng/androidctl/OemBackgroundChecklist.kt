package com.rainng.androidctl

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

internal data class OemBackgroundChecklistItemSpec(
    val key: String,
    @param:StringRes val labelRes: Int,
)

internal data class OemBackgroundChecklistSpec(
    @param:StringRes val titleRes: Int,
    @param:StringRes val introRes: Int,
    @param:StringRes val noteRes: Int,
    val items: List<OemBackgroundChecklistItemSpec>,
)

internal val oemBackgroundChecklistSpec =
    OemBackgroundChecklistSpec(
        titleRes = R.string.section_oem_manual_checklist,
        introRes = R.string.oem_manual_checklist_intro,
        noteRes = R.string.oem_manual_checklist_note,
        items =
            listOf(
                OemBackgroundChecklistItemSpec(
                    key = "background_lock",
                    labelRes = R.string.oem_checklist_background_lock,
                ),
                OemBackgroundChecklistItemSpec(
                    key = "auto_start",
                    labelRes = R.string.oem_checklist_auto_start,
                ),
                OemBackgroundChecklistItemSpec(
                    key = "associated_launch",
                    labelRes = R.string.oem_checklist_associated_launch,
                ),
                OemBackgroundChecklistItemSpec(
                    key = "power_management",
                    labelRes = R.string.oem_checklist_power_management,
                ),
            ),
    )

@Composable
internal fun OemBackgroundChecklistCard(
    modifier: Modifier = Modifier,
    spec: OemBackgroundChecklistSpec = oemBackgroundChecklistSpec,
) {
    val checkedStates = remember(spec) { mutableStateMapOf<String, Boolean>() }
    StatusInfoCard(
        titleRes = spec.titleRes,
        content = {
            Column(
                modifier = modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    text = stringResource(spec.introRes),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    spec.items.forEach { item ->
                        OemChecklistItemRow(
                            checked = checkedStates[item.key] == true,
                            labelRes = item.labelRes,
                            onCheckedChange = { checkedStates[item.key] = it },
                        )
                    }
                }
                Text(
                    text = stringResource(spec.noteRes),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
    )
}

@Composable
private fun OemChecklistItemRow(
    checked: Boolean,
    @StringRes labelRes: Int,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier =
            Modifier
                .fillMaxWidth()
                .toggleable(
                    value = checked,
                    role = Role.Checkbox,
                    onValueChange = onCheckedChange,
                ).padding(vertical = 2.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Checkbox(checked = checked, onCheckedChange = null)
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            text = stringResource(labelRes),
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
            softWrap = true,
            overflow = TextOverflow.Visible,
            maxLines = Int.MAX_VALUE,
        )
    }
}
