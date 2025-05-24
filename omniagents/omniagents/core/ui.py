import json
import flet as ft
from flet import animation as animation  # ensure ft.animation submodule loaded
from typing import Dict, Optional, Any

# --- Shared Message Class ---
class Message:
    def __init__(self, user_name: str, text: str, message_type: str):
        self.user_name = user_name
        self.text = text
        self.message_type = message_type

# --- Shared Tool Call Display ---
class ToolCallDisplay(ft.Container):
    def __init__(
        self,
        tool_name: str,
        args_str: str,
        icon_map: Optional[Dict[str, str]] = None, # Make icon map optional
        color: str = ft.Colors.BLUE, # Parameterize color
        default_icon: str = ft.Icons.BUILD # Default icon if not in map
    ):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Use provided icon_map or empty dict
        effective_icon_map = icon_map or {}
        icon = effective_icon_map.get(tool_name, default_icon)

        # Prepare icon container
        icon_container = ft.Container(
            content=ft.Icon(name=icon, size=16, color=ft.Colors.WHITE),
            width=28,
            height=28,
            border_radius=14,
            bgcolor=color, # Use parameterized color
            alignment=ft.alignment.center,
        )

        # Prepare text content based on args_str
        if args_str:
            text_content = ft.Column([
                ft.Text(
                    f"Using {tool_name}",
                    weight="w500",
                    size=13,
                    color=ft.Colors.BLACK87
                ),
                ft.Text(
                    f"{args_str}",
                    size=12,
                    color=ft.Colors.BLACK54,
                ),
            ], spacing=2, tight=True, expand=True)
            row_alignment = ft.CrossAxisAlignment.START # Default alignment when args are present
        else:
            text_content = ft.Text(
                f"Using {tool_name}",
                weight="w500",
                size=13,
                color=ft.Colors.BLACK87
            )
            row_alignment = ft.CrossAxisAlignment.CENTER # Center align when no args

        # Construct the main row
        tool_card_content = ft.Row(
            [
                icon_container,
                ft.Container(width=10), # Spacer
                text_content,
            ],
            spacing=0,
            alignment=row_alignment, # Apply conditional alignment
        )

        tool_card = ft.Container(
            content=tool_card_content,
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.04, color), # Use parameterized color
        )

        self.content = tool_card

# --- Shared Tool Output Display ---
class ToolOutputDisplay(ft.Container):
    def __init__(self, output_text):
        super().__init__()
        self.padding = 0
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        if len(output_text) > 1000:
            display_text = output_text[:1000] + "... [output truncated]"
        else:
            display_text = output_text

        self.content = ft.Container(
            content=ft.Text(
                display_text,
                selectable=True,
                size=13,
                color=ft.Colors.BLACK87,
                font_family="SF Mono, Menlo, Monaco, Consolas, monospace"
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
            border=ft.border.all(0.5, ft.Colors.BLACK12),
        )

# --- Shared Chat Message Display ---
class ChatMessage(ft.Container):
    def __init__(self, message: Message, page: ft.Page = None, assistant_name: str = "Assistant"):
        super().__init__()
        self.animate = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT)
        self.padding = ft.padding.only(bottom=0)

        is_user = message.user_name == "You"
        is_system = message.user_name == "System"

        if is_user:
            # Build the user bubble
            bubble = ft.Container(
                content=ft.Text(message.text, selectable=True, size=14, color=ft.Colors.BLACK87),
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
                border_radius=18,
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
            )
            # Wrap bubble in expanding container to constrain width and right-align with a right margin
            self.content = ft.Row(
                [
                    ft.Container(
                        content=bubble,
                        expand=True,
                        alignment=ft.alignment.center_right,
                    )
                ]
            )

        elif is_system:
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=14, color=ft.Colors.BLACK54, italic=True),
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
            )
            alignment = ft.MainAxisAlignment.START
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

        else: # Assistant message (could be "Assistant" or "Indie", etc.)
            content_area = ft.Container(
                content=ft.Markdown(
                    value=message.text, selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                    code_theme=ft.MarkdownCodeTheme.GITHUB,
                    on_tap_link=lambda e: page.launch_url(e.data) if page else None,
                ),
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
            )
            alignment = ft.MainAxisAlignment.START
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

# --- Tool Approval Request UI ---
class ToolApprovalRequest(ft.Container):
    def __init__(self, tool_name, args_str, on_approve, on_approve_session, on_reject):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)
        
        # Use orange color for approval requests
        color = ft.Colors.ORANGE

        # Prepare icon container with more appropriate icon for approval
        icon_container = ft.Container(
            content=ft.Icon(name=ft.icons.VERIFIED_USER, size=16, color=ft.Colors.WHITE),
            width=28,
            height=28,
            border_radius=14,
            bgcolor=color,
            alignment=ft.alignment.center,
        )

        # Prepare text content similar to ToolCallDisplay
        text_content = ft.Column([
            ft.Text(
                f"Approve {tool_name}",
                weight="w500",
                size=13,
                color=ft.Colors.BLACK87
            ),
            ft.Text(
                f"{args_str if args_str else 'No parameters'}",
                size=12,
                color=ft.Colors.BLACK54,
                selectable=True,
            ),
        ], spacing=2, tight=True, expand=True)

        # Construct the main row exactly like ToolCallDisplay
        main_content = ft.Row(
            [
                icon_container,
                ft.Container(width=10),  # Spacer
                text_content,
            ],
            spacing=0,
            alignment=ft.CrossAxisAlignment.START,
        )
        
        # Add buttons row at the bottom with proper spacing
        # Reordered buttons to make "Approve Once" the primary option
        buttons_row = ft.Row(
            [
                ft.TextButton(
                    text="Reject",
                    on_click=on_reject,
                    style=ft.ButtonStyle(
                        color=ft.Colors.BLACK54,
                    ),
                ),
                ft.TextButton(
                    text="Always",
                    on_click=on_approve_session,
                    style=ft.ButtonStyle(
                        color=color,
                    ),
                ),
                ft.FilledButton(
                    text="Approve Once",
                    on_click=on_approve,
                    height=36,  # Increased height to prevent text cutoff
                    style=ft.ButtonStyle(
                        bgcolor=color,
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.END,
        )
        
        # Stack content and buttons
        content_column = ft.Column([
            main_content,
            buttons_row,
        ], spacing=8, tight=True)

        # Create tool card container (same structure as ToolCallDisplay)
        tool_card = ft.Container(
            content=content_column,
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.04, color),
            border=ft.border.all(1, ft.Colors.with_opacity(0.08, color)),
        )

        self.content = tool_card


# --- Collapsible Card and Tool Interaction Display ---
class CollapsibleCard(ft.Container):
    """A simple collapsible card used for tool call/results."""

    def __init__(self, icon_color: str, icon: str, title: str,
                 body: str | None = None, body_widget: ft.Control | None = None,
                 initially_expanded: bool = False):
        super().__init__()
        self.icon_color = icon_color
        self.icon = icon
        self.title = title
        self.body = body
        self.body_widget = body_widget
        self.expanded = initially_expanded
        self._refresh()

    def _refresh(self):
        arrow = None
        if self.body or self.body_widget:
            arrow = ft.Icon(
                ft.icons.EXPAND_LESS if self.expanded else ft.icons.EXPAND_MORE,
                size=16,
                color=ft.Colors.BLACK54,
            )

        header_items = [
            ft.Container(
                content=ft.Icon(name=self.icon, size=12, color=self.icon_color),
                width=20,
                height=20,
                alignment=ft.alignment.center,
                border=ft.border.all(1, ft.Colors.with_opacity(0.8, self.icon_color)),
                border_radius=10,
            ),
            ft.Container(width=4),
            ft.Text(self.title, weight="w500", size=13, color=ft.Colors.BLACK87, expand=True),
        ]
        if arrow:
            header_items.append(arrow)

        header = ft.Row(
            header_items,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.START if self.expanded else ft.CrossAxisAlignment.CENTER,
        )

        children = [header]
        if self.expanded and (self.body or self.body_widget):
            children.append(ft.Container(height=6))
            if self.body_widget is not None:
                children.append(self.body_widget)
            else:
                children.append(
                    ft.Markdown(
                        value=self.body or "",
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                        code_theme=ft.MarkdownCodeTheme.GITHUB,
                    )
                )

        self.content = ft.Column(children, spacing=0, tight=True)
        self.padding = ft.padding.symmetric(horizontal=16, vertical=12)
        self.border_radius = 8
        self.bgcolor = ft.Colors.with_opacity(0.04, self.icon_color)
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.08, self.icon_color))
        self.on_click = self._toggle if arrow else None

    def _toggle(self, _):
        self.expanded = not self.expanded
        self._refresh()
        self.update()


def _json_key_value_widget(data: Dict[str, Any]) -> ft.Column:
    children = []
    for k, v in data.items():
        if not isinstance(v, str):
            try:
                v_str = json.dumps(v, indent=2)
            except Exception:
                v_str = str(v)
        else:
            v_str = v
        fenced = f"```text\n{v_str}\n```"
        children.append(ft.Text(k, weight="w500", size=12, color=ft.Colors.BLACK54))
        children.append(
            ft.Markdown(
                value=fenced,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                code_theme=ft.MarkdownCodeTheme.GITHUB,
            )
        )
    return ft.Column(children, spacing=4, tight=True)


def _build_json_section(label: str, text: str, color: str) -> list[ft.Control]:
    parts: list[ft.Control] = []
    parts.append(ft.Text(label, weight="w500", size=12, color=color))
    parts.append(ft.Container(height=4))
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        pass
    if isinstance(parsed, dict):
        parts.append(_json_key_value_widget(parsed))
    else:
        display = text
        if len(display) > 1000:
            display = display[:1000] + "... [output truncated]"
        fenced = f"```text\n{display}\n```"
        parts.append(
            ft.Markdown(
                value=fenced,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                code_theme=ft.MarkdownCodeTheme.GITHUB,
            )
        )
    parts.append(ft.Container(height=12))
    return parts


class ToolInteractionDisplay(CollapsibleCard):
    """Combined tool call and result display with collapsible body."""

    def __init__(self, tool_name: str, args_str: str, icon_map: Optional[Dict[str, str]] = None,
                 color: str = ft.Colors.BLUE, output_text: str | None = None):
        self.tool_name = tool_name
        self.args_str = args_str
        self.output_text = output_text
        self.icon_map = icon_map or {}
        self.theme_color = color
        super().__init__(icon_color=color,
                         icon=self.icon_map.get(tool_name, ft.Icons.BUILD),
                         title=f"Tool {tool_name}",
                         body=None,
                         body_widget=None,
                         initially_expanded=False)

    def has_output(self) -> bool:
        return self.output_text is not None

    def set_output(self, output_text: str):
        self.output_text = output_text
        self._refresh()
        self.update()

    def _refresh(self):
        body_parts: list[ft.Control] = []
        if self.args_str:
            body_parts.extend(_build_json_section("input", self.args_str, self.theme_color))
        if self.output_text is not None:
            body_parts.extend(_build_json_section("output", self.output_text, ft.Colors.GREEN))
        if body_parts and isinstance(body_parts[-1], ft.Container):
            body_parts.pop()  # remove trailing spacer

        self.icon_color = ft.Colors.GREEN if self.output_text is not None else self.theme_color
        self.icon = ft.Icons.CHECK if self.output_text is not None else self.icon_map.get(self.tool_name, ft.Icons.BUILD)
        self.title = f"Tool {self.tool_name}"
        self.body_widget = ft.Column(body_parts, spacing=4, tight=True) if body_parts else None
        self.body = None
        super()._refresh()
