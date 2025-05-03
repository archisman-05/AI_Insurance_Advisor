import streamlit as st
import google.generativeai as genai
import json
import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
from datetime import datetime
import threading
import schedule
import time
import yaml


st.set_page_config(
    page_title="Health Insurance Advisor",
    page_icon="🏥",
    layout="wide"
)

# Hardcode the API key directly in the code
API_KEY = "Your_API_Key"

# Configure Gemini API with the hardcoded key
genai.configure(api_key=API_KEY)

# Initialize session states
if "messages" not in st.session_state:
    st.session_state.messages = []

if "user_profile" not in st.session_state:
    st.session_state.user_profile = {
        "age": None,
        "gender": None,
        "pre_existing_conditions": [],
        "family_size": None,
        "budget": None,
        "coverage_amount": None,
        "preferred_features": []
    }

if "latest_irdai_data" not in st.session_state:
    st.session_state.latest_irdai_data = []

if "claim_settlement_data" not in st.session_state:
    st.session_state.claim_settlement_data = []

if "last_update" not in st.session_state:
    st.session_state.last_update = None


# Load insurance database from YAML file
def load_insurance_database():
    try:
        with open("insurance_database.yml", "r", encoding="utf-8") as file:
            return yaml.safe_load(file)
    except Exception as e:
        st.error(f"Error loading insurance database: {str(e)}")
        return []


# Load the insurance database
INSURANCE_DATABASE = load_insurance_database()


# Function to create Gemini model
def get_gemini_model():
    """Create and return a Gemini model instance."""
    try:
        # Changed from gemini-1.5-pro to gemini-2.0-flash
        model = genai.GenerativeModel('gemini-2.0-flash')
        return model
    except Exception as e:
        st.error(f"Error initializing Gemini model: {str(e)}")
        return None


# Function with exponential backoff for API calls
def generate_with_backoff(model, prompt, max_retries=3):
    """Make API calls with exponential backoff for rate limiting."""
    retries = 0
    while retries < max_retries:
        try:
            return model.generate_content(prompt)
        except Exception as e:
            if "429" in str(e) or "Resource exhausted" in str(e) or "quota" in str(e).lower():
                wait_time = (2 ** retries) * 5  # Exponential backoff
                print(f"Rate limit hit, waiting {wait_time} seconds...")
                time.sleep(wait_time)
                retries += 1
            else:
                raise e

    class FallbackResponse:
        def __init__(self):
            self.text = "I'm currently experiencing high demand. Please try again in a few minutes."

    return FallbackResponse()


# Function to fetch latest insurance data from IRDAI
def fetch_irdai_data():
    try:
        url = "https://irdai.gov.in/health-insurance-products"
        response = requests.get(url)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Extract and parse the latest insurance data
            insurance_data = []
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) > 5:  # Ensure row has enough columns
                        company = cols[2].text.strip()
                        policy = cols[4].text.strip()
                        date = cols[5].text.strip()
                        pdf_link = cols[7].find('a')['href'] if cols[7].find('a') else ""

                        insurance_data.append({
                            "company": company,
                            "policy": policy,
                            "date": date,
                            "pdf_link": pdf_link
                        })

            # Update the database with new information
            st.session_state.latest_irdai_data = insurance_data
            st.session_state.last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            return insurance_data
        else:
            return []
    except Exception as e:
        print(f"Error fetching IRDAI data: {str(e)}")
        return []


def fetch_claim_settlement_data():
    try:
        # Based on search result, Ditto provides updated claim settlement ratios
        url = "https://joinditto.in/health-insurance/companies/"
        response = requests.get(url)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Find the table with claim settlement data
            tables = soup.find_all('table')

            claim_data = []
            for table in tables:
                rows = table.find_all('tr')
                for row in rows[1:]:  # Skip header row
                    cols = row.find_all('td')
                    if len(cols) >= 5:
                        company = cols[0].text.strip()
                        csr = cols[1].text.strip()
                        hospitals = cols[2].text.strip()
                        premium = cols[3].text.strip()

                        claim_data.append({
                            "company": company,
                            "claim_settlement_ratio": csr,
                            "network_hospitals": hospitals,
                            "premium": premium
                        })

            st.session_state.claim_settlement_data = claim_data
            return claim_data
        else:
            return []
    except Exception as e:
        print(f"Error fetching claim settlement data: {str(e)}")
        return []


# Enhanced function to scrape premium data from insurance company websites
def scrape_premium_data():
    try:
        # List of major health insurance company websites
        insurance_websites = {
            "HDFC ERGO": "https://www.hdfcergo.com/health-insurance/plans",
            "Star Health": "https://www.starhealth.in/health-insurance-plans",
            "Aditya Birla": "https://www.adityabirlacapital.com/health-insurance/plans",
            "Bajaj Allianz": "https://www.bajajallianz.com/health-insurance-plans.html",
            "ICICI Lombard": "https://www.icicilombard.com/health-insurance/health-plans",
            "Tata AIG": "https://www.tataaig.com/health-insurance/health-plans",
            "SBI General": "https://www.sbigeneral.in/health-insurance/health-plans",
            "Care Health": "https://www.careinsurance.com/health-insurance-policies.html"
        }

        premium_data = []

        for company, url in insurance_websites.items():
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, timeout=10)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')

                    # Look for policy cards/containers (generic selectors that need customization per site)
                    policy_containers = soup.select('.plan-card, .product-card, .policy-card, .insurance-plan, .card')

                    for container in policy_containers:
                        try:
                            # Extract policy name
                            policy_name_elem = container.select_one('h2, h3, .plan-name, .policy-name, .title')
                            policy_name = policy_name_elem.text.strip() if policy_name_elem else "Unknown Policy"

                            # Extract premium information
                            premium_elem = container.select_one('.premium, .price, .amount, .rate')
                            premium = premium_elem.text.strip() if premium_elem else "Premium not found"

                            # Extract coverage information
                            coverage_elem = container.select_one('.coverage, .sum-insured, .cover-amount')
                            coverage = coverage_elem.text.strip() if coverage_elem else "Coverage not found"

                            # Extract features
                            feature_elems = container.select('li, .feature, .benefit')
                            features = [elem.text.strip() for elem in feature_elems]

                            premium_data.append({
                                "company": company,
                                "policy_name": policy_name,
                                "premium": premium,
                                "coverage": coverage,
                                "features": features[:5],  # Limit to top 5 features
                                "last_updated": datetime.now().strftime("%Y-%m-%d")
                            })
                        except Exception as e:
                            print(f"Error parsing policy from {company}: {str(e)}")
                            continue
            except Exception as e:
                print(f"Error scraping {company}: {str(e)}")
                continue

        return premium_data
    except Exception as e:
        print(f"Error in premium scraping: {str(e)}")
        return []


# Function to fetch terms and conditions from insurance company websites
def fetch_terms_and_conditions(company_name):
    try:
        # This is a simplified example - in practice, you'd need to map company names to their websites
        company_websites = {
            "HDFC ERGO": "https://www.hdfcergo.com/health-insurance",
            "Aditya Birla": "https://www.adityabirlacapital.com/health-insurance",
            "Bajaj Allianz": "https://www.bajajallianz.com/health-insurance.html",
            "Care": "https://www.careinsurance.com/health-insurance-policies.html",
            "Niva Bupa": "https://www.nivabupa.com/health-insurance",
            "Star Health": "https://www.starhealth.in/health-insurance",
            "ICICI Lombard": "https://www.icicilombard.com/health-insurance",
            "SBI General": "https://www.sbigeneral.in/health-insurance",
            "Tata AIG": "https://www.tataaig.com/health-insurance",
            "Max Bupa": "https://www.maxbupa.com/health-insurance",
            "Religare": "https://www.religarehealthinsurance.com/health-insurance",
        }

        # Try to find the best match for company name
        best_match = None
        for key in company_websites:
            if key.lower() in company_name.lower() or company_name.lower() in key.lower():
                best_match = key
                break

        if best_match:
            url = company_websites[best_match]
            response = requests.get(url)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                # Look for terms and conditions sections
                terms_sections = soup.find_all(['div', 'section'], class_=lambda c: c and (
                        'terms' in c.lower() or 'conditions' in c.lower()))

                terms_text = []
                for section in terms_sections:
                    terms_text.append(section.get_text(strip=True))

                # If no specific terms sections found, try to extract from policy pages
                if not terms_text:
                    # Try to find links to terms and conditions pages
                    terms_links = soup.find_all('a', text=lambda t: t and (
                            'terms' in t.lower() or 'conditions' in t.lower()))

                    for link in terms_links:
                        if 'href' in link.attrs:
                            terms_url = link['href']
                            if not terms_url.startswith('http'):
                                # Handle relative URLs
                                if terms_url.startswith('/'):
                                    base_url = '/'.join(url.split('/')[:3])
                                    terms_url = base_url + terms_url
                                else:
                                    terms_url = url + '/' + terms_url

                            terms_response = requests.get(terms_url)
                            if terms_response.status_code == 200:
                                terms_soup = BeautifulSoup(terms_response.text, 'html.parser')
                                terms_content = terms_soup.find(['div', 'section'], class_=lambda c: c and (
                                        'content' in c.lower() or 'main' in c.lower()))
                                if terms_content:
                                    terms_text.append(terms_content.get_text(strip=True))

                return {
                    "company": company_name,
                    "website": url,
                    "terms_and_conditions": '\n'.join(
                        terms_text) if terms_text else "Terms and conditions not found. Please visit the company website."
                }
            else:
                return {
                    "company": company_name,
                    "website": url,
                    "terms_and_conditions": "Could not access the website. Please visit the company website directly."
                }
        else:
            # Search the web for the company's health insurance page
            search_url = f"https://www.google.com/search?q={company_name}+health+insurance+india+official+website"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            search_response = requests.get(search_url, headers=headers)

            if search_response.status_code == 200:
                search_soup = BeautifulSoup(search_response.text, 'html.parser')
                search_results = search_soup.select('.g .yuRUbf a')

                if search_results:
                    company_url = search_results[0]['href']
                    return {
                        "company": company_name,
                        "website": company_url,
                        "terms_and_conditions": f"Please visit {company_url} for detailed terms and conditions."
                    }

            return {
                "company": company_name,
                "website": "Website not found",
                "terms_and_conditions": "Company website information not available."
            }
    except Exception as e:
        print(f"Error fetching terms for {company_name}: {str(e)}")
        return {
            "company": company_name,
            "website": "Error accessing website",
            "terms_and_conditions": f"Error: {str(e)}"
        }


def search_web_for_insurance(query):
    try:
        search_terms = f"health insurance India {query}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        search_url = f"https://www.google.com/search?q={search_terms.replace(' ', '+')}"
        response = requests.get(search_url, headers=headers)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            search_results = []
            for result in soup.select('div.g'):
                title_element = result.select_one('h3')
                if title_element:
                    title = title_element.get_text()

                    snippet_element = result.select_one('div.IsZvec')
                    snippet = snippet_element.get_text() if snippet_element else ""

                    search_results.append(f"Title: {title}\nSnippet: {snippet}\n")

            return "\n".join(search_results[:5])  # Return top 5 results
        else:
            return "Could not retrieve search results."
    except Exception as e:
        return f"Error searching the web: {str(e)}"


def analyze_pre_existing_conditions(conditions, model):
    if not conditions:
        return "No pre-existing conditions specified."

    prompt = f"""
    As a health insurance expert in India, analyze the following pre-existing medical conditions:
    {', '.join(conditions)}

    For each condition, provide:
    1. How this condition typically affects health insurance eligibility in India
    2. Expected waiting periods for this condition across major insurers
    3. Which insurance companies in India are known to have better coverage for this condition
    4. Any special considerations or riders that might be needed

    Format your response in a clear, structured manner.
    """

    response = generate_with_backoff(model, prompt)
    return response.text


def fetch_irdai_policy_details(policy_name=None, company_name=None):
    try:
        url = "https://irdai.gov.in/health-insurance-products"
        response = requests.get(url)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            tables = soup.find_all('table')

            policy_details = []
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) > 5:
                        row_company = cols[2].text.strip()
                        row_policy = cols[4].text.strip()

                        if (policy_name and policy_name.lower() in row_policy.lower()) or \
                                (company_name and company_name.lower() in row_company.lower()) or \
                                (not policy_name and not company_name):

                            pdf_link = ""
                            if len(cols) > 7 and cols[7].find('a'):
                                pdf_link = cols[7].find('a')['href']

                            policy_details.append({
                                "company": row_company,
                                "policy_name": row_policy,
                                "approval_date": cols[5].text.strip(),
                                "pdf_link": pdf_link,
                                "uin": cols[3].text.strip() if len(cols) > 3 else ""
                            })

            return policy_details
        else:
            return []
    except Exception as e:
        print(f"Error fetching IRDAI policy details: {str(e)}")
        return []


def match_insurance_to_requirements(user_profile, model):
    # Get the latest data
    latest_irdai_data = st.session_state.get('latest_irdai_data', [])
    claim_settlement_data = st.session_state.get('claim_settlement_data', [])
    premium_data = scrape_premium_data()  # Get real-time premium data

    # Combine with our static database for comprehensive information
    combined_data = INSURANCE_DATABASE.copy()

    # Update with latest claim settlement ratios
    for company_data in combined_data:
        for claim_data in claim_settlement_data:
            if company_data["name"].lower() in claim_data["company"].lower() or claim_data["company"].lower() in \
                    company_data["name"].lower():
                company_data["claim_settlement_ratio"] = claim_data["claim_settlement_ratio"]
                company_data["network_hospitals"] = claim_data["network_hospitals"]
                break

    # Update with latest premium data
    for company_data in combined_data:
        company_premium_data = [p for p in premium_data if
                                company_data["name"].lower() in p["company"].lower() or p["company"].lower() in
                                company_data["name"].lower()]

        if company_premium_data:
            for policy in company_data["policies"]:
                matching_premium = next((p for p in company_premium_data if
                                         policy["name"].lower() in p["policy_name"].lower() or p[
                                             "policy_name"].lower() in policy["name"].lower()), None)

                if matching_premium:
                    policy["premium_range"] = matching_premium["premium"]
                    policy["last_updated"] = matching_premium["last_updated"]
                    if matching_premium["features"]:
                        policy["special_features"] = ", ".join(matching_premium["features"])

    prompt = f"""
    As an expert health insurance advisor in India, analyze the following user profile and the latest insurance data.
    Recommend the top 3 most suitable health insurance policies based on the user's requirements.

    User Profile:
    - Age: {user_profile['age']}
    - Gender: {user_profile['gender']}
    - Pre-existing conditions: {', '.join(user_profile['pre_existing_conditions']) if user_profile['pre_existing_conditions'] else 'None'}
    - Family size: {user_profile['family_size']}
    - Budget: {user_profile['budget']}
    - Desired coverage amount: {user_profile['coverage_amount']}
    - Preferred features: {', '.join(user_profile['preferred_features']) if user_profile['preferred_features'] else 'None'}

    Latest Insurance Data (as of {st.session_state.get('last_update', 'today')}):
    {json.dumps(combined_data, indent=2)}

    For each recommendation, provide:
    1. Company name and policy name
    2. Why this policy is suitable for the user (match to specific requirements)
    3. Key benefits that align with user preferences
    4. Latest claim settlement ratio and network hospitals information
    5. Any limitations or drawbacks to be aware of
    6. Estimated premium range based on user profile
    7. Waiting period for pre-existing conditions
    8. Company website for more information

    Also include a comparison table of the three recommended policies highlighting key differences.
    """

    response = generate_with_backoff(model, prompt)
    recommendations_text = response.text

    company_names = re.findall(r'(?:Company|Insurer): ([A-Za-z\s]+)', recommendations_text)
    if not company_names:
        company_names = [company["name"] for company in combined_data[:3]]

    terms_data = []
    for company in company_names:
        company = company.strip()
        terms = fetch_terms_and_conditions(company)
        terms_data.append(terms)

    recommendations_text += "\n\n## Terms and Conditions\n\n"
    for terms in terms_data:
        recommendations_text += f"### {terms['company']}\n"
        recommendations_text += f"**Website:** {terms['website']}\n\n"
        recommendations_text += "**Key Terms and Conditions:**\n"

        if len(terms['terms_and_conditions']) > 1000:
            summary_prompt = f"Summarize the following health insurance terms and conditions in bullet points, focusing on the most important aspects for consumers:\n\n{terms['terms_and_conditions'][:5000]}"
            summary_response = generate_with_backoff(model, summary_prompt)
            recommendations_text += summary_response.text + "\n\n"
        else:
            recommendations_text += terms['terms_and_conditions'] + "\n\n"

    return recommendations_text


def schedule_data_updates():
    # Update data daily
    schedule.every().day.at("00:00").do(fetch_irdai_data)
    schedule.every().day.at("00:30").do(fetch_claim_settlement_data)
    schedule.every().day.at("01:00").do(scrape_premium_data)

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()


# UI Components
def render_chat_interface():
    st.title("Health Insurance Advisor")
    st.subheader("Your AI assistant for finding the best health insurance in India")

    # Test API key and show status
    try:
        model = get_gemini_model()
        test_response = generate_with_backoff(model, "Test")
        st.success("API key is working correctly")
    except Exception as e:
        st.error(f"API key error: {str(e)}")
        st.stop()

    # Show data freshness information
    if st.session_state.last_update:
        st.info(f"Insurance data last updated: {st.session_state.last_update}")

    # Sidebar for user profile
    with st.sidebar:
        st.header("Your Profile")

        # Collect user information
        st.session_state.user_profile["age"] = st.number_input("Age", min_value=1, max_value=100, value=30)
        st.session_state.user_profile["gender"] = st.selectbox("Gender", ["Male", "Female", "Other"])

        # Pre-existing conditions
        pre_existing_input = st.text_input("Pre-existing conditions (comma separated)")
        if pre_existing_input:
            st.session_state.user_profile["pre_existing_conditions"] = [condition.strip() for condition in
                                                                        pre_existing_input.split(",")]

        st.session_state.user_profile["family_size"] = st.number_input("Family size", min_value=1, max_value=10,
                                                                       value=1)
        st.session_state.user_profile["budget"] = st.selectbox("Monthly budget",
                                                               ["₹500-₹1,000", "₹1,000-₹2,000", "₹2,000-₹5,000",
                                                                "₹5,000+"])
        st.session_state.user_profile["coverage_amount"] = st.selectbox("Desired coverage amount",
                                                                        ["₹5 Lakhs", "₹10 Lakhs", "₹25 Lakhs",
                                                                         "₹50 Lakhs", "₹1 Crore"])

        # Preferred features
        features = st.multiselect("Preferred features",
                                  ["Return of premium", "No co-payment", "Maternity coverage",
                                   "OPD coverage", "Critical illness cover", "International coverage"])
        st.session_state.user_profile["preferred_features"] = features

        # Analyze pre-existing conditions button
        if st.session_state.user_profile["pre_existing_conditions"] and st.button("Analyze Pre-existing Conditions"):
            with st.spinner("Analyzing conditions..."):
                analysis = analyze_pre_existing_conditions(st.session_state.user_profile["pre_existing_conditions"],
                                                           model)
                st.session_state.messages.append({"role": "assistant", "content": analysis})

        # Get recommendations button
        if st.button("Get Recommendations"):
            with st.spinner("Analyzing insurance options..."):
                # Get recommendations
                recommendations = match_insurance_to_requirements(st.session_state.user_profile, model)

                # Add system message with recommendations
                st.session_state.messages.append({"role": "assistant", "content": recommendations})

        # Manual data refresh button
        if st.button("Refresh Insurance Data"):
            with st.spinner("Fetching latest insurance data..."):
                irdai_data = fetch_irdai_data()
                claim_data = fetch_claim_settlement_data()
                premium_data = scrape_premium_data()

                st.success(
                    f"Data refreshed successfully! Found {len(irdai_data)} IRDAI policies, {len(claim_data)} claim settlement records, and {len(premium_data)} premium details.")

    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Chat input
    if prompt := st.chat_input("Ask about health insurance in India..."):
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Generate response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                # Prepare context for the model
                context = """
                You are an expert health insurance advisor in India. Your task is to help users find the best health insurance 
                policies based on their requirements, budget, and medical conditions. Focus only on health insurance available 
                in India with coverage up to 1 crore. Be specific about policy details, premiums, waiting periods for pre-existing 
                conditions, and unique features. When recommending policies, always mention the insurance company name and 
                specific policy details.
                """

                # Combine context, chat history, and current prompt
                full_prompt = context + "\n\n"
                for msg in st.session_state.messages[-5:]:  # Include last 5 messages for context
                    full_prompt += f"{msg['role']}: {msg['content']}\n\n"

                # If the query seems to require web search
                if any(keyword in prompt.lower() for keyword in
                       ["compare", "best", "recommend", "which company", "policy for"]):
                    search_results = search_web_for_insurance(prompt)
                    full_prompt += f"\nRecent web search results: {search_results}\n\n"

                # If query is about terms and conditions
                if any(keyword in prompt.lower() for keyword in
                       ["terms", "conditions", "policy details", "exclusions"]):
                    # Extract company name if mentioned
                    company_match = re.search(r'(terms|conditions|policy|exclusions) (?:of|for) ([A-Za-z\s]+)',
                                              prompt.lower())
                    if company_match:
                        company_name = company_match.group(2).strip().title()
                        terms_data = fetch_terms_and_conditions(company_name)

                        # Add terms data to the prompt
                        full_prompt += f"\n\nTerms and conditions for {company_name}:\n"
                        full_prompt += f"Website: {terms_data['website']}\n"
                        full_prompt += f"Terms: {terms_data['terms_and_conditions'][:2000]}...\n\n"

                # If query is about specific policy details
                policy_match = re.search(r'(policy|plan|details) (?:of|for|about) ([A-Za-z\s]+)', prompt.lower())
                if policy_match:
                    policy_name = policy_match.group(2).strip().title()
                    policy_details = fetch_irdai_policy_details(policy_name=policy_name)

                    if policy_details:
                        full_prompt += f"\n\nIRDAI details for {policy_name}:\n"
                        full_prompt += json.dumps(policy_details, indent=2) + "\n\n"

                response = generate_with_backoff(model, full_prompt)
                response_text = response.text

                # Display response
                st.markdown(response_text)

                # Add assistant response to chat history
                st.session_state.messages.append({"role": "assistant", "content": response_text})


# Main function
def main():
    # Initialize latest data if not already in session state
    if not st.session_state.latest_irdai_data:
        with st.spinner("Fetching latest insurance data..."):
            st.session_state.latest_irdai_data = fetch_irdai_data()

    if not st.session_state.claim_settlement_data:
        with st.spinner("Fetching claim settlement data..."):
            st.session_state.claim_settlement_data = fetch_claim_settlement_data()

    # Schedule regular updates
    schedule_data_updates()

    render_chat_interface()


if __name__ == "__main__":
    main()
