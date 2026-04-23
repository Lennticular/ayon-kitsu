# -*- coding: utf-8 -*-
import re

import gazu
import pyblish.api

from ayon_kitsu.pipeline import KitsuPublishContextPlugin


class IntegrateKitsuNote(KitsuPublishContextPlugin):
    """Integrate Kitsu Note"""

    order = pyblish.api.IntegratorOrder
    label = "Kitsu Note and Status"
    families = ["kitsu"]

    # status settings
    set_status_note = False
    note_status_shortname = "wfa"
    # farm-related settings
    set_status_on_farm = False
    farm_note_status_shortname = ""
    status_change_conditions = {
        "status_conditions": [],
        "family_requirements": [],
    }

    # comment settings
    custom_comment_template = {
        "enabled": False,
        "comment_template": "{comment}",
    }
    farm_comment_template = {
        "enabled": False,
        "comment_template": "Send version {version} to farm",
    }

    def format_publish_comment(self, instance, template_settings=None):
        """Format the instance's publish comment

        Formats `instance.data` against the custom template.
        """

        def replace_missing_key(match):
            """If key is not found in kwargs, set None instead"""
            key = match.group(1)
            if key not in instance.data:
                self.log.warning(
                    "Key '{}' was not found in instance.data "
                    "and will be rendered as an empty string "
                    "in the comment".format(key)
                )
                return ""
            else:
                return str(instance.data[key])

        if template_settings is None:
            template_settings = self.custom_comment_template

        template = template_settings["comment_template"]
        pattern = r"\{([^}]*)\}"
        return re.sub(pattern, replace_missing_key, template)

    def process(self, context):
        farm_render_targets = {
            "farm",
            "farm_split",
            "local_export_farm_render",
        }

        # Some hosts (e.g. Houdini) may generate derived instances for notes
        # that no longer carry direct farm markers. Detect farm intent from
        # the whole publish context and use it as a fallback.
        context_is_farm_publish = False
        for ctx_instance in context:
            ctx_families = set(
                [ctx_instance.data.get("family")]
                + ctx_instance.data.get("families", [])
            )
            ctx_render_target = str(
                ctx_instance.data.get("creator_attributes", {}).get(
                    "render_target", ""
                )
            ).lower()
            if (
                "render.farm" in ctx_families
                or "farm" in ctx_families
                or ctx_instance.data.get("farm") is True
                or ctx_render_target in farm_render_targets
            ):
                context_is_farm_publish = True
                break

        for instance in context:
            # Check if instance is a review or farm publish and that kitsu is present
            # Allow a match to primary family or any of families
            instance_families = set(
                [instance.data["family"]] + instance.data.get("families", [])
            )
            render_target = str(
                instance.data.get("creator_attributes", {}).get("render_target", "")
            ).lower()
            is_farm_publish = (
                "render.farm" in instance_families
                or "farm" in instance_families
                or instance.data.get("farm") is True
                or render_target in farm_render_targets
                or context_is_farm_publish
            )
            is_review_publish = "review" in instance_families

            if "kitsu" not in instance_families or not (
                is_review_publish or is_farm_publish
            ):
                continue

            kitsu_task = instance.data.get("kitsuTask")
            if not kitsu_task:
                continue

            # Get note status, by default uses the task status for the note
            # if it is not specified in the configuration
            shortname = kitsu_task["task_status"]["short_name"].upper()
            note_status = kitsu_task["task_status_id"]

            # Check if any status condition is not met
            allow_status_change = True
            for status_cond in (
                self.status_change_conditions["status_conditions"]
            ):
                condition = status_cond["condition"] == "equal"
                match = status_cond["short_name"].upper() == shortname
                if match and not condition or condition and not match:
                    allow_status_change = False
                    break

            if allow_status_change:
                # Get families
                context_families = {
                    instance.data.get("family")
                    for instance in context
                    if instance.data.get("publish")
                }

                # Check if any family requirement is met

                for family_requirement in (
                    self.status_change_conditions["family_requirements"]
                ):
                    condition = family_requirement["condition"] == "equal"
                    requirement_family = family_requirement.get(
                        "family", family_requirement.get("product_type", "")
                    ).lower()

                    for family in context_families:
                        match = requirement_family == family
                        if match and not condition or condition and not match:
                            allow_status_change = False
                            break

                    if allow_status_change:
                        break

            # Choose whether to set status for review or for farm based on families
            if is_farm_publish and self.set_status_on_farm and allow_status_change:
                kitsu_status = gazu.task.get_task_status_by_short_name(
                    self.farm_note_status_shortname.strip()
                )
                if kitsu_status:
                    note_status = kitsu_status["id"]
                    self.log.info(f"Note Kitsu status (farm): {note_status}")
                else:
                    self.log.info(
                        f"Cannot find {self.farm_note_status_shortname} status."
                        " The status will not be changed!"
                    )
            elif is_review_publish and self.set_status_note and allow_status_change:
                kitsu_status = gazu.task.get_task_status_by_short_name(
                    self.note_status_shortname.strip()
                )
                if kitsu_status:
                    note_status = kitsu_status["id"]
                    self.log.info(f"Note Kitsu status: {note_status}")
                else:
                    self.log.info(
                        f"Cannot find {self.note_status_shortname} status."
                        " The status will not be changed!"
                    )

            # Get comment text body
            publish_comment = instance.data.get("comment")
            if is_farm_publish and self.farm_comment_template["enabled"]:
                publish_comment = self.format_publish_comment(
                    instance, self.farm_comment_template
                )
            elif self.custom_comment_template["enabled"]:
                publish_comment = self.format_publish_comment(instance)

            if not publish_comment:
                self.log.debug("Comment is not set.")
            else:
                self.log.debug(f"Comment is `{publish_comment}`")

            # Add comment to kitsu task
            self.log.debug(f'Add new note in tasks id {kitsu_task["id"]}')
            kitsu_comment = gazu.task.add_comment(
                kitsu_task, note_status, comment=publish_comment
            )

            instance.data["kitsuComment"] = kitsu_comment
