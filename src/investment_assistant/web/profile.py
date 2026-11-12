"""Questionnaire vocabulary and explicit, non-inferred ticker profile precedence."""

from investment_assistant.domain.profile import InvestorProfile

UNKNOWN = ["Not sure", "Prefer not to say"]
STEPS = [
    {"title": "Goals and interests", "description": "What would you like to work towards?",
     "fields": [
         ("goal", "Your main goal", ["Long-term growth", "Investment income", "Saving for a goal", "Learning"]),
         ("assets", "Assets you want to understand", ["Company shares", "Funds and ETFs", "Commodities", "A mix of assets"])]},
    {"title": "Time horizon", "description": "How long could you leave money invested?",
     "fields": [
         ("horizon", "Your time horizon", ["Under 1 year", "1–3 years", "3–5 years", "Over 5 years"]),
         ("need_soon", "Might you need the money sooner?", ["Yes", "No"])]},
    {"title": "Financial situation", "description": "Optional ranges help put examples in context. No employer or exact salary needed.",
     "fields": [
         ("employment", "Employment status (optional)", ["Employed", "Self-employed", "Studying", "Retired", "Not currently employed"]),
         ("currency", "Currency", ["GBP", "USD", "EUR", "CNY", "Another currency"]),
         ("income", "Annual income range (in your selected currency)", ["Under 25,000", "25,000–50,000", "50,000–100,000", "Over 100,000"]),
         ("stability", "Income stability", ["Mostly stable", "Variable", "Currently uncertain"]),
         ("emergency", "Emergency savings", ["Under 3 months of expenses", "3–6 months of expenses", "Over 6 months of expenses"]),
         ("budget", "Monthly investment budget (selected currency)", ["No regular budget", "Under 100", "100–500", "500–1,000", "Over 1,000"])]},
    {"title": "Risk", "description": "Willingness to take risk and capacity to absorb losses are different. We never infer either from your job or income.",
     "fields": [
         ("reaction", "Imagine an investment falls 20%. How might you feel?", ["Very uncomfortable", "Concerned; want to review", "Comfortable with fluctuations"]),
         ("loss", "What loss would feel acceptable? (illustration only)", ["Less than 5%", "5–10%", "10–20%", "More than 20%"]),
         ("capacity", "Could a loss affect essential spending?", ["Yes, materially", "Possibly", "No, essentials are covered"])]},
    {"title": "Experience and preferences", "description": "Choose how you would like ideas explained.",
     "fields": [
         ("experience", "Your investing experience", ["Beginner", "Intermediate", "Experienced"]),
         ("existing", "Existing investments", ["None yet", "Shares", "Funds or ETFs", "Several asset types"]),
         ("depth", "Preferred explanation depth", ["Step by step", "Balanced", "Concise"])]},
]
CHOICES = {key: options + UNKNOWN for step in STEPS for key, _, options in step["fields"]}


def validate_answers(answers: dict) -> dict:
    if set(answers) - set(CHOICES):
        raise ValueError("Unknown questionnaire field.")
    for key, value in answers.items():
        if value not in CHOICES[key]:
            raise ValueError("Choose one of the available answers.")
    return answers


def effective_profile(ticker: str, shared: dict, explicit: InvestorProfile) -> InvestorProfile:
    answers = shared.get("answers", {}) if shared.get("personalise") else {}
    mapping = {"goal": "investment_objective", "horizon": "intended_holding_period",
               "loss": "maximum_acceptable_loss", "currency": "investment_currency"}
    values = {target: answers[key] for key, target in mapping.items()
              if answers.get(key) and answers[key] not in UNKNOWN}
    # Employment and income never set risk_tolerance, and neither does an illustrative reaction.
    values.update(explicit.model_dump(exclude_none=True))
    return InvestorProfile.model_validate({**values, "ticker": ticker})


class EffectiveProfiles:
    def __init__(self, profile: InvestorProfile):
        self.profile = profile

    def load(self, ticker: str) -> InvestorProfile:
        return self.profile
