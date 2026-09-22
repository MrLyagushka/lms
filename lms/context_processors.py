from .models import Theme


def active_theme(request):
    """Передаёт активную тему оформления во все шаблоны."""
    theme = Theme.objects.filter(is_active=True).first()
    if not theme:
        return {"theme": None}
    return {
        "theme": {
            "primary_color": theme.primary_color,
            "secondary_color": theme.secondary_color,
            "background_color": theme.background_color,
            "text_color": theme.text_color,
            "card_background": theme.card_background,
            "navbar_background": theme.navbar_background,
            "font_family": theme.font_family,
            "border_radius": theme.border_radius,
        }
    }
