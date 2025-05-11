import flet as ft
from typing import Dict, Optional

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