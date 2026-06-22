"""Curated career-site configurations for target companies."""

from config import USER_PROFILE

# ats: greenhouse | lever | ashby | smartrecruiters
# slug: board identifier on that ATS platform
KNOWN_ATS = {
    "Airbnb": {"ats": "greenhouse", "slug": "airbnb"},
    "CRED": {"ats": "lever", "slug": "cred"},
    "Databricks": {"ats": "greenhouse", "slug": "databricks"},
    "Freshworks": {"ats": "smartrecruiters", "slug": "Freshworks"},
    "LinkedIn": {"ats": "greenhouse", "slug": "linkedin"},
    "Meesho": {"ats": "lever", "slug": "meesho"},
    "PhonePe": {"ats": "greenhouse", "slug": "phonepe"},
    "Postman": {"ats": "greenhouse", "slug": "postman"},
    "Razorpay": {"ats": "greenhouse", "slug": "razorpaysoftwareprivatelimited"},
    "Rubrik": {"ats": "greenhouse", "slug": "rubrik"},
    "ServiceNow": {"ats": "smartrecruiters", "slug": "ServiceNow"},
    "Snowflake": {"ats": "ashby", "slug": "snowflake"},
    "Uber": {"ats": "smartrecruiters", "slug": "Uber"},
    "Unacademy": {"ats": "smartrecruiters", "slug": "Unacademy"},
}

# Career page URLs used to auto-discover ATS slugs when not in KNOWN_ATS
CAREER_URLS = {
    "Google": "https://careers.google.com/jobs/results/",
    "Meta": "https://www.metacareers.com/jobs",
    "Microsoft": "https://careers.microsoft.com/us/en/search-results",
    "Amazon": "https://www.amazon.jobs/en/search",
    "Apple": "https://jobs.apple.com/en-us/search",
    "Netflix": "https://jobs.netflix.com/search",
    "Uber": "https://www.uber.com/us/en/careers/list/",
    "Airbnb": "https://careers.airbnb.com/",
    "LinkedIn": "https://careers.linkedin.com/",
    "Salesforce": "https://careers.salesforce.com/en/jobs/",
    "Databricks": "https://www.databricks.com/company/careers/open-positions",
    "Snowflake": "https://careers.snowflake.com/us/en/search-results",
    "Atlassian": "https://www.atlassian.com/company/careers/all-jobs",
    "Adobe": "https://careers.adobe.com/us/en/search-results",
    "NVIDIA": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "SAP": "https://jobs.sap.com/",
    "VMware": "https://careers.vmware.com/main/jobs",
    "Cisco": "https://jobs.cisco.com/jobs/SearchJobs",
    "ServiceNow": "https://careers.servicenow.com/",
    "Intuit": "https://jobs.intuit.com/search-jobs",
    "PayPal": "https://paypal.eightfold.ai/careers",
    "Oracle": "https://careers.oracle.com/en/sites/jobsearch/jobs",
    "Expedia Group": "https://careers.expediagroup.com/jobs/",
    "Electronic Arts": "https://ea.gr8people.com/jobs",
    "Qualcomm": "https://careers.qualcomm.com/careers",
    "Walmart Global Tech": "https://tech.walmart.com/content/technology-us/en/tools/careers.html",
    "JPMorgan Chase": "https://careers.jpmorgan.com/us/en/students/programs/software-engineer-fulltime",
    "Morgan Stanley": "https://morganstanley.eightfold.ai/careers",
    "Goldman Sachs": "https://higher.gs.com/results",
    "Cohesity": "https://careers.cohesity.com/",
    "Rubrik": "https://www.rubrik.com/company/careers",
    "Zoho": "https://www.zoho.com/careers/job-openings.html",
    "Freshworks": "https://careers.freshworks.com/",
    "Chargebee": "https://www.chargebee.com/careers/",
    "Kissflow": "https://kissflow.com/careers/",
    "Postman": "https://www.postman.com/company/careers/",
    "BrowserStack": "https://www.browserstack.com/careers",
    "Razorpay": "https://razorpay.com/jobs/",
    "CRED": "https://careers.cred.club/",
    "Meesho": "https://careers.meesho.com/",
    "Swiggy": "https://careers.swiggy.com/",
    "Myntra": "https://careers.myntra.com/",
    "PhonePe": "https://www.phonepe.com/careers/",
    "ClearTax": "https://cleartax.in/s/careers",
    "Unacademy": "https://unacademy.com/careers",
    "ShareChat": "https://sharechat.com/careers",
}

ALL_COMPANIES = USER_PROFILE.get("target_companies", [])


def get_career_config(company: str) -> dict:
    config = {"company": company, "career_url": CAREER_URLS.get(company, "")}

    if company in KNOWN_ATS:
        config.update(KNOWN_ATS[company])
    else:
        config["ats"] = None
        config["slug"] = None

    return config
