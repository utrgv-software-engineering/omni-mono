import flet as ft
from dataclasses import dataclass, field
from typing import List, Callable, Dict, Any, Optional
from collections import defaultdict
from .specs import AgentSpec, SettingField

class SettingsScreen(ft.View):
    def __init__(self,
                 spec: AgentSpec,
                 fields: List[SettingField],
                 initial_values: Dict[str, Any],
                 on_save: Callable[[Dict[str, Any]], None],
                 on_back: Callable[[Any], None]):
        super().__init__()

        self.route = "/settings"
        self.bgcolor = ft.colors.with_opacity(0.98, ft.Colors.WHITE)
        self.padding = ft.padding.all(0)

        self.spec = spec
        self.fields_spec = fields
        self.on_save = on_save
        self.on_back = on_back
        self.initial_values = initial_values
        self.text_fields: Dict[str, ft.TextField] = {}

        # --- Create Header ---
        settings_header = ft.Container(
            content=ft.SafeArea(
                # --- Wrap Row in a Container and Column like app_base.py ---
                content=ft.Column([
                    ft.Container(
                        content=ft.Row([
                            ft.IconButton(
                                icon=ft.Icons.ARROW_BACK,
                                icon_color=ft.Colors.WHITE,
                                icon_size=20,
                                on_click=self._go_back,
                                style=ft.ButtonStyle(
                                    padding=8,
                                    shape=ft.CircleBorder(),
                                ),
                                height=40,
                                width=40,
                            ),
                            ft.Text("Settings", color=ft.Colors.WHITE, size=20, weight="w500"),
                            ft.TextButton(
                                text="Save",
                                on_click=self._save_settings,
                                style=ft.ButtonStyle(color=ft.Colors.WHITE),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER
                        ),
                        # --- Apply height and padding to inner container ---
                        padding=ft.padding.symmetric(horizontal=20, vertical=0),
                        height=64,
                        alignment=ft.alignment.center, # Center content vertically in inner container
                    )
                ]),
                bottom=False
            ),
            # --- Remove height and padding from outer container ---
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                colors=[ft.Colors.INDIGO_900, self.spec.theme_color, ft.colors.with_opacity(0.8, self.spec.theme_color)],
            ),
            # padding=ft.padding.symmetric(horizontal=20, vertical=0), # Removed
            # height=64, # Removed
            alignment=ft.alignment.center,
            shadow=ft.BoxShadow(
                spread_radius=0, blur_radius=4,
                color=ft.Colors.with_opacity(0.3, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
        )

        # --- MOVED UI Building Logic Inside Init --- 

        # --- Group Fields and Build UI --- 
        grouped_fields = defaultdict(list)
        all_groups = [] # Keep track of group order
        for field_spec in self.fields_spec:
            group_name = field_spec.group or "General" # Default group if none specified
            if group_name not in grouped_fields:
                all_groups.append(group_name)
            grouped_fields[group_name].append(field_spec)
            # Create the text field instance
            self.text_fields[field_spec.key] = ft.TextField(
                label=field_spec.label,
                value=self.initial_values.get(field_spec.key, field_spec.default),
                password=field_spec.secret,
                can_reveal_password=field_spec.secret,
                border_color=ft.Colors.BLUE_200,
                expand=True,
                hint_text=field_spec.hint or field_spec.default,
            )

        # Build controls for each group
        grouped_controls = []
        for group_name in all_groups:
            field_controls_in_group = []
            for i, field_spec in enumerate(grouped_fields[group_name]):
                tf = self.text_fields[field_spec.key]
                is_last_in_group = (i == len(grouped_fields[group_name]) - 1)
                field_controls_in_group.append(
                    ft.Container(
                        content=tf,
                        padding=ft.padding.symmetric(horizontal=16, vertical=8),
                        # Add border only if not the last item in the group
                        border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.BLACK12)) if not is_last_in_group else None,
                    )
                )

            # Add group header
            grouped_controls.append(
                ft.Container(
                    content=ft.Text(
                        group_name.upper(), # Display group name as header
                        size=12,
                        weight="w500",
                        color=ft.Colors.BLACK54,
                    ),
                    margin=ft.margin.only(left=16, bottom=6, top=20 if len(grouped_controls) > 0 else 0), # Add top margin for subsequent groups
                )
            )

            # Add the container for the fields in this group
            grouped_controls.append(
                 ft.Container(
                    content=ft.Column(field_controls_in_group, spacing=0),
                    bgcolor=ft.Colors.WHITE,
                    border_radius=10,
                    shadow=ft.BoxShadow(
                        spread_radius=0, blur_radius=2,
                        color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK), offset=ft.Offset(0, 1)
                    ),
                    margin=ft.margin.only(bottom=6) # Smaller margin below group box
                )
            )
            # Optionally add a hint/footer for the group if needed
            # last_field_hint = grouped_fields[group_name][-1].hint
            # if last_field_hint:
            #     grouped_controls.append(ft.Container(
            #         content=ft.Text(last_field_hint, size=12, color=ft.Colors.BLACK45),
            #         margin=ft.margin.only(left=16, top=6, bottom=24)
            #     ))
            # else:
            grouped_controls.append(ft.Container(height=18)) # Space between groups


        # --- Create Scrollable Content --- 
        settings_content = ft.ListView(
            controls=[
                ft.Container(height=12),
                ft.Container(
                    content=ft.Text(
                        "Configure API credentials and settings",
                        size=14,
                        color=ft.Colors.BLACK54,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    margin=ft.margin.only(bottom=0, top=10),
                    expand=True,
                ),
                *grouped_controls, # Unpack the generated group controls
                ft.Container(height=30),
            ],
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=20),
        )

        # View controls: Header followed by content list (remove SafeArea from content)
        self.controls = [
            settings_header,
            settings_content, # Directly add settings_content
        ]
        # View properties are set directly on the View instance
        self.spacing = 0 # Prevent spacing between header and content

    # --- Make the save method async --- #
    async def _save_settings(self, e):
        settings = {key: tf.value for key, tf in self.text_fields.items()}
        if self.on_save:
            # --- Await the callback --- #
            await self.on_save(settings)

    def _go_back(self, e):
        if self.on_back:
            self.on_back(e) 