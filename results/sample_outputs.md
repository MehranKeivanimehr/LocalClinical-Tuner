# Sample outputs (held-out test set)

Randomly selected (seeded) test encounters. Notes are synthetic or public role-played dialogues.

## SUMMARY - `mts-train-1066` (mts-dialog)

<details><summary>Source note</summary>

```text
Doctor: You do not look so well today. What seems to be the problem?
Patient: Well‚ I am having a hard time. My whole body aches and I am in pain.
Doctor: Do you have a history of drug use?
Patient: Well yes. I am detoxing.
Doctor: What kind of drugs have you been using?
Patient: Heroin.
Doctor: Okay. How long have you been using?
Patient: I don't feel well. Maybe a year. I can't sleep or eat.
Doctor: Have you had a fever, chills, vomiting or diarrhea?
Patient: I think I have a fever. I feel chilled. I have had some diarrhea.
```
</details>

**Reference**

```text
The patient is experiencing body aches and pain. They report a history of heroin use and are currently detoxing. They have had trouble sleeping, eating, and have had a fever, chills, and diarrhea. The assessment and plan are not documented.
```

**Base (Qwen2.5-7B-Instruct)**

```text
The 35-year-old male presented with complaints of body aches and pain, reporting a history of heroin use during detoxification. He mentioned difficulty sleeping and eating for about a year. The patient also reported feeling chilled and having a fever along with some diarrhea. The assessment suggests acute symptoms possibly related to his detoxification process. The plan includes monitoring his symptoms and providing supportive care.
```

**SFT**

```text
The patient is a person with a history of heroin use who is currently detoxing. They report widespread body aches, pain, difficulty sleeping, and loss of appetite. The patient also mentions feeling feverish and chilled, along with some diarrhea. They are experiencing significant distress and are seeking medical attention.
```

**SFT + ORPO**

```text
The patient is a male who presents with generalized aches and pain. He reports a history of heroin use and is currently detoxing. He states he has been using for approximately one year and is experiencing difficulty sleeping and eating. The patient also mentions possible fever, chills, and diarrhea.
```

## SUMMARY - `syn-00255` (synthetic)

<details><summary>Source note</summary>

```text
ENT/ophtho clinic, recurrent tonsillitis f/u
17-year-old male, here w/ mom. Anxious, asking lots of questions thruout visit (about 15 Qs on surgery, pain, bleeding, school, soccer).

CC: recurrent sore throat x 1 yr, wants to discuss tonsil surgery

HPI:
- c/o sore throat w/ fever and odynophagia, pt says ~4 episodes this yr. CORRECTION per chart + mom's log: 7 episodes in past 12 mo, 3 w/ RADT+ for GAS, 4 clinical dx
- last episode 5 wks ago, tx'd w/ amoxicillin, completed. 2 earlier courses of azithromycin
- missed ~15 school days this yr
- c/o halitosis and tonsil stones
- c/o itchy watery eyes, worse in spring, wears glasses
- denies fever, trismus, drooling, voice change, or ear pain today
- denies snoring, witnessed apnea, daytime sleepiness
- no eye pain, no photophobia, no vision change, no joint pain, no rash

PMH: allergic rhinitis, allergic conjunctivitis, mild intermittent asthma
Allergies: NKDA
FHx: older sister s/p tonsillectomy
Social: HS junior, plays soccer, no tobacco/vaping, no EtOH

Meds on arrival:
- cetirizine 10 mg daily (pt first said 5 mg, bottle checked = 10 mg)
- fluticasone nasal spray 1 spray each nostril daily
- olopatadine 0.1% eye drops 1 drop each eye BID PRN
- montelukast 10 mg qHS
- albuterol inhaler 2 puffs PRN
- ibuprofen 400 mg q6h PRN throat pain

Vitals: BP 118/70, HR 82, RR 16, Temp 98.4 F, SpO2 99% on RA

Exam:
- Oropharynx: tonsils 3+ bilat w/ cryptic debris, no exudate today, no peritonsillar bulge, uvula midline
- Neck: tender anterior cervical LAD ~1 cm bilat, no mass
- Ears: TMs clear bilat
- Nose: pale boggy turbinates, no polyps
- Eyes: VA 20/20 OU w/ glasses, mild conjunctival injection OU, no discharge, PERRL, EOMI
- Lungs CTA bilat, no wheeze

Data:
- RADT today neg
- throat cx sent, pending
- monospot neg
- CBC from last wk: WBC 9.2, Hgb 14.6, plt 288

A/P:
1. Recurrent tonsillitis, 7 episodes in 12 mo (meets Paradise criteria), w/ chronic tonsil hypertrophy and cryptic tonsils
 - recommend bilateral tonsillectomy; consent discussion done, risks incl bleeding, post-op pain, anesthesia reviewed, recovery ~10-14 days
 - pre-op: CBC done, add PT/INR
 - f/u throat cx result
 - cont ibuprofen 400 mg q6h PRN until surgery
2. Allergic rhinitis: increase fluticasone to 2 sprays each nostril daily, cont cetirizine 10 mg daily
3. Allergic conjunctivitis: cont olopatadine 1 drop each eye BID PRN, cool compresses, ophtho f/u PRN or sooner if vision change
4. Mild intermittent asthma: D/C montelukast (pt reports poor [...]
```
</details>

**Reference**

```text
A 17-year-old male with 7 episodes of tonsillitis in the past 12 months was seen in the ENT and ophthalmology clinic, anxious and asking many questions. He first reported about 4 episodes, but chart review and his mother's log corrected the count to 7, with 3 positive rapid strep tests. Exam showed 3+ cryptic tonsils without exudate, tender anterior cervical nodes, pale boggy turbinates, and mild conjunctival injection with 20/20 vision, and rapid strep and monospot were negative today. He also has allergic rhinitis, allergic conjunctivitis, and mild intermittent asthma. Bilateral tonsillectomy was recommended, montelukast was stopped, fluticasone was increased to 2 sprays per nostril daily, and follow-up in 2 weeks was arranged.
```

**Base (Qwen2.5-7B-Instruct)**

```text
A 17-year-old male presented to the ENT/ophtho clinic for follow-up on recurrent tonsillitis, with 7 episodes in the past year, including 3 positive for Group A Streptococcus. He has a history of allergic rhinitis, allergic conjunctivitis, and mild intermittent asthma. The patient is scheduled for bilateral tonsillectomy, with pre-operative labs ordered, and will continue ibuprofen for throat pain. His asthma medication was discontinued due to poor sleep and increased anxiety. He was also advised to increase his nasal steroid use and continue eye drops for conjunctivitis. The visit concluded with detailed reassurance about the surgery and a follow-up appointment in two weeks.
```

**SFT**

```text
A 17-year-old male with allergic rhinitis, allergic conjunctivitis, and mild intermittent asthma was seen in ENT/ophtho clinic for follow-up of recurrent tonsillitis with 7 episodes in 12 months, including 3 positive rapid antigen tests. He is anxious and asked many questions about surgery, pain, bleeding, and recovery. Exam showed 3+ tonsils with cryptic debris, tender anterior cervical nodes, and mild conjunctival injection, and the RADT was negative. The assessment was recurrent tonsillitis meeting Paradise criteria, with allergic rhinitis and allergic conjunctivitis. The plan includes tonsillectomy counseling, a CBC, a throat culture, continued ibuprofen, increased fluticasone, stopped montelukast, and follow-up in 2 weeks.
```

**SFT + ORPO**

```text
A 17-year-old male with allergic rhinitis, allergic conjunctivitis, and mild intermittent asthma presented to ENT/ophtho clinic for follow-up of recurrent tonsillitis with 7 episodes in 12 months, including 3 positive rapid antigen tests. He was anxious and asked many questions about surgery, pain, bleeding, and recovery. Exam showed 3+ tonsils with cryptic debris, tender anterior cervical nodes, and mild conjunctival injection, and the RADT was negative. The assessment was recurrent tonsillitis meeting Paradise criteria, with allergic rhinitis and allergic conjunctivitis. The plan is bilateral tonsillectomy after a CBC and PT/INR, increased fluticasone, continued olopatadine and albuterol, stopped montelukast, and follow-up in 2 weeks.
```

## SOAP - `mts-train-411` (mts-dialog)

<details><summary>Source note</summary>

```text
Doctor: Good afternoon, sir. My nurse has it written down here that that you're a thirty three year old black male, is that correct?
Patient: Yes, doctor, that's correct.
Doctor: Great, what seems to be the problem today, sir?
Patient: Well, I think I'm having some sexual dysfunction. I was wondering if Cialis would help.
Doctor: What makes you think this, sir?
Patient: Well, I did a quiz online on the Cialis website and I didn't score in the normal range, so I figured I would come in.
Doctor: Is there anything else of note, sir?
Patient: Well, there may be some decreased desire, but honestly, I don't think that's the main problem here. I'd like to have my erections last longer, doctor.
Doctor: What was this quiz like?
Patient: Well, on the quiz, I said that less than half of the time I'm able to get an erection and keep an erection after penetration.
Doctor: Are you having difficulty maintaining an erection until completion?
Patient: Well, I'd say there's slight difficulty.
Doctor: Okay, do you have any significant medical conditions in the past that you have been treated for?
Patient: No, not that I know of.
Doctor: What about testicular infections?
Patient: No, that sounds painful, but no.
Doctor: Have you had any traumatic injuries to the groin?
Patient: No, not that I can remember.
Doctor: What about hernias? You can get them from playing sports or lifting weights. Have you ever had one of these?
Patient: No, that doesn't sound familiar either.
```
</details>

**Reference**

```text
SUBJECTIVE:
Chief complaint: Sexual dysfunction. History of present illness: Patient reports difficulty achieving and maintaining an erection after penetration and wants his erections to last longer. He took an online Cialis quiz, which indicated abnormal results. Pertinent positives: Concern about sexual performance. Pertinent negatives: Denies significant medical conditions, testicular infections, traumatic groin injuries, or hernias. Social history: Not documented. Medications: Not documented. Allergies: Not documented.

OBJECTIVE:
Not documented.

ASSESSMENT:
Not documented.

PLAN:
Not documented.
```

**Base (Qwen2.5-7B-Instruct)**

```text
SUBJECTIVE:
Chief Complaint: Sexual dysfunction.
History of Present Illness: Patient reports difficulty maintaining erections, with less than half of the time being able to get an erection and keep it after penetration. There is slight difficulty maintaining an erection until completion. Decreased desire is noted but not considered the primary issue.
Pertinent Positives: Difficulty getting and maintaining erections. Negative for significant medical conditions, testicular infections, and hernias.
Relevant History: Patient is a 33-year-old black male. No past medical conditions. No traumatic injuries to the groin. No history of testicular infections or hernias. Online quiz suggested potential erectile dysfunction.
Medications: Not documented.
Allergies: Not documented.
Social History: Not documented.

OBJECTIVE:
Vital Signs: Not documented.
Examination Findings: Not documented.
Test Results: Not documented.

ASSESSMENT:
Erectile dysfunction.

PLAN:
1. Consider prescribing Cialis for erectile dysfunction.
2. Schedule a follow-up appointment in one month to reassess symptoms.
```

**SFT**

```text
SUBJECTIVE:
33-year-old black male presents with concerns about sexual dysfunction. He took an online quiz on the Cialis website and scored outside the normal range, leading him to seek evaluation. He reports decreased erectile function, with difficulty maintaining an erection until completion. He denies significant medical conditions, testicular infections, traumatic groin injuries, or hernias.

OBJECTIVE:
Not documented.

ASSESSMENT:
Not documented.

PLAN:
Not documented.
```

**SFT + ORPO**

```text
SUBJECTIVE:
The patient is a 33-year-old black male who reports sexual dysfunction. He states he did an online quiz on the Cialis website and scored outside the normal range, leading him to seek evaluation. He mentions decreased desire but states that maintaining erections is his primary concern. He reports slight difficulty maintaining erections until completion. He denies a history of testicular infections, traumatic groin injuries, or hernias.

OBJECTIVE:
Not documented.

ASSESSMENT:
Not documented.

PLAN:
Not documented.
```

## SOAP - `syn-00118` (synthetic)

<details><summary>Source note</summary>

```text
34-year-old female in ED for abd cramping + loose stools after gluten exposure; known celiac disease (dx 5 yrs ago). Poor GI f/u: last GI visit 2 yrs ago. No car, bus doesn't run to the GI clinic 40 mi away, missed 3 appts d/t transport.

HPI:
- Ate pasta at family dinner ~5 d ago, then cramping, bloating, 4-5 loose BMs/day x 4 d, fatigue.
- Denies blood in stool, melena, fever, vomiting.
- Wt stable per pt. LMP 2 wk ago.

PMH: celiac disease, iron deficiency anemia, vit D deficiency.
Meds: ferrous sulfate 325 mg daily, vitamin D3 1000 units daily. NKDA.

Vitals: Temp 98.4 F, HR 96, BP 112/70, RR 16, SpO2 99% RA.
Exam: mildly dry mucous membranes, diffuse mild abd tenderness, hyperactive bowel sounds; no guarding, no rebound, soft.
ED labs: Hgb 10.4, ferritin 9, K 3.5, Cr 0.7, tTG-IgA 62 U/mL (elevated), 25-OH vit D 18 ng/mL (low), urine hCG neg.
ED course: NS 1 L bolus, sx improved, tolerating PO.

Impression: celiac disease w/ symptomatic gluten exposure + mild dehydration. Persistent iron deficiency anemia + vit D deficiency from malabsorption.
Dispo: d/c home.

F/u:
- Strict gluten-free diet reinforced. Dietitian referral (telehealth).
- Ferrous sulfate changed from 325 mg daily to 325 mg every other day (better absorption).
- Increase vitamin D3 from 1000 units to 2000 units daily.
- GI referral w/ telehealth visit (video or phone) so she does not need to travel. SW consulted for ride vouchers for any in-person visit.
- Repeat tTG-IgA, ferritin, CBC in 3 mo at lab near home.
- Return if temp over 100.4 F, unable to keep fluids down, or worsening belly pain.
```
</details>

**Reference**

```text
SUBJECTIVE:
34-year-old female in the ED with abdominal cramping, bloating, 4-5 loose bowel movements a day for 4 days and fatigue after eating pasta about 5 days ago; known celiac disease diagnosed 5 years ago. Last GI visit 2 years ago; no car, the bus does not run to the GI clinic 40 miles away, and she missed 3 appointments for lack of transport. Denies blood in stool, melena, fever and vomiting; weight stable. PMH celiac disease, iron deficiency anemia, vitamin D deficiency. Medications ferrous sulfate 325 mg daily and vitamin D3 1000 units daily. No known drug allergies.

OBJECTIVE:
Temp 98.4 F, HR 96, BP 112/70, RR 16, SpO2 99% on room air. Mildly dry mucous membranes, diffuse mild abdominal tenderness, hyperactive bowel sounds, no guarding or rebound, abdomen soft. ED labs: Hgb 10.4, ferritin 9, K 3.5, Cr 0.7, tTG-IgA 62 U/mL (elevated), 25-OH vitamin D 18 ng/mL (low), urine hCG negative. NS 1 L bolus given with symptom improvement and tolerating PO.

ASSESSMENT:
Celiac disease with symptomatic gluten exposure and mild dehydration; persistent iron deficiency anemia and vitamin D deficiency from malabsorption.

PLAN:
1. Discharge home
2. Reinforce a strict gluten-free diet and refer to a dietitian by telehealth
3. Change ferrous sulfate from 325 mg daily to 325 mg every other day for better absorption
4. Increase vitamin D3 from 1000 units to 2000 units daily
5. GI referral with a telehealth visit by video or phone so she does not need to travel; social work consulted for ride vouchers for any in-person visit
6. Repeat tTG-IgA, ferritin and CBC in 3 months at a lab near home
7. Return if temperature over 100.4 F, unable to keep fluids down, or worsening belly pain
```

**Base (Qwen2.5-7B-Instruct)**

```text
SUBJECTIVE:
Chief Complaint: Abdominal cramping and loose stools after gluten exposure.
History of Present Illness: Ate pasta at family dinner 5 days ago, followed by cramping, bloating, 4-5 loose bowel movements per day for 4 days, and fatigue. Denies blood in stool, melena, fever, or vomiting. Weight stable. Last menstrual period 2 weeks ago.
Pertinent Positives: Gluten exposure, abdominal cramping, bloating, loose stools, fatigue.
Pertinent Negatives: Denies blood in stool, melena, fever, vomiting.
Relevant History: Known celiac disease diagnosed 5 years ago. Poor gastrointestinal follow-up: last GI visit 2 years ago. No car, bus doesn't run to the GI clinic 40 miles away, missed 3 appointments due to transportation issues. Iron deficiency anemia, vitamin D deficiency. Medications: ferrous sulfate 325 mg daily, vitamin D3 1000 units daily. Allergies: None known. Social History: No car, relies on public transportation which is unreliable.

OBJECTIVE:
Vitals: Temperature 98.4 F, Heart rate 96, Blood pressure 112/70, Respiratory rate 16, SpO2 99% RA.
Examination: Mildly dry mucous membranes, diffuse mild abdominal tenderness, hyperactive bowel sounds; no guarding, no rebound, soft abdomen. Test Results: Hemoglobin 10.4, Ferritin 9, Potassium 3.5, Creatinine 0.7, tTG-IgA 62 U/mL (elevated), 25-OH vitamin D 18 ng/mL (low), urine hCG negative.

ASSESSMENT:
Celiac disease with symptomatic gluten exposure + mild dehydration. Persistent iron deficiency anemia + vitamin D deficiency from malabsorption.

PLAN:
1. Reinforce strict gluten-free diet.
2. Refer to dietitian for telehealth consultation.
3. Change ferrous sulfate dosage from 325 mg daily to 325 mg every other day.
4. Increase vitamin D3 dosage from 1000 units daily to 2000 units daily.
5. Schedule telehealth visit with GI specialist.
6. Arrange ride vouchers from social work for any in-person visits.
7. Order repeat tTG-IgA, ferritin, CBC in 3 months at a lab near home.
8. Follow up if temperature exceeds 100.4 F, unable to keep fluids down, or worsening abdominal pain.
```

**SFT**

```text
SUBJECTIVE:
34-year-old female with known celiac disease (diagnosed 5 years ago) presents with abdominal cramping and loose stools after gluten exposure. Poor gastrointestinal follow-up: last GI visit 2 years ago. No car and the bus does not run to the GI clinic 40 miles away, causing her to miss 3 appointments due to transportation. Ate pasta at a family dinner about 5 days ago, then cramping, bloating, 4-5 loose stools per day for 4 days, and fatigue. Denies blood in stool, melena, fever, and vomiting. Weight stable. Last menstrual period 2 weeks ago. PMH: celiac disease, iron deficiency anemia, vitamin D deficiency. Takes ferrous sulfate 325 mg daily and vitamin D3 1000 units daily. NKDA.

OBJECTIVE:
Temp 98.4 F, HR 96, BP 112/70, RR 16, SpO2 99% on room air. Mildly dry mucous membranes, diffuse mild abdominal tenderness, hyperactive bowel sounds, no guarding, no rebound, abdomen soft. Hgb 10.4, ferritin 9, K 3.5, Cr 0.7, tTG-IgA 62 U/mL (elevated), 25-OH vitamin D 18 ng/mL (low), urine hCG negative. After an NS 1 L bolus in the ED symptoms improved and she is tolerating oral intake.

ASSESSMENT:
Celiac disease with symptomatic gluten exposure and mild dehydration. Persistent iron deficiency anemia and vitamin D deficiency from malabsorption.

PLAN:
1. Discharge home
2. Strict gluten-free diet reinforced; dietitian referral by telehealth
3. Change ferrous sulfate from 325 mg daily to 325 mg every other day
4. Increase vitamin D3 from 1000 units to 2000 units daily
5. GI referral with telehealth visit (video or phone)
6. Social work consulted for ride vouchers for any in-person visit
7. Repeat tTG-IgA, ferritin, and CBC in 3 months at a lab near home
8. Return for temperature over 100.4 F, inability to keep fluids down, or worsening belly pain
```

**SFT + ORPO**

```text
SUBJECTIVE:
34-year-old female with known celiac disease (diagnosed 5 years ago) presents with abdominal cramping and loose stools after gluten exposure. Last GI visit was 2 years ago; no car and the bus does not run to the clinic 40 miles away, causing missed appointments. Ate pasta at a family dinner about 5 days ago, then cramping, bloating, 4-5 loose stools per day for 4 days, and fatigue. Denies blood in stool, melena, fever, and vomiting. Weight stable, last menstrual period 2 weeks ago. PMH: celiac disease, iron deficiency anemia, vitamin D deficiency. Takes ferrous sulfate 325 mg daily and vitamin D3 1000 units daily. NKDA.

OBJECTIVE:
Temp 98.4 F, HR 96, BP 112/70, RR 16, SpO2 99% on room air. Mildly dry mucous membranes, diffuse mild abdominal tenderness, hyperactive bowel sounds, no guarding, no rebound, soft abdomen. Hgb 10.4, ferritin 9, K 3.5, Cr 0.7, tTG-IgA 62 U/mL (elevated), 25-OH vitamin D 18 ng/mL (low), urine hCG negative. After an IV fluid bolus, symptoms improved and she is tolerating oral intake.

ASSESSMENT:
Celiac disease with symptomatic gluten exposure and mild dehydration. Persistent iron deficiency anemia and vitamin D deficiency from malabsorption.

PLAN:
1. Discharge home
2. Strict gluten-free diet reinforced; dietitian referral by telehealth
3. Change ferrous sulfate from 325 mg daily to 325 mg every other day
4. Increase vitamin D3 from 1000 units to 2000 units daily
5. GI referral with telehealth visit (video or phone)
6. Social work consulted for ride vouchers for any in-person visit
7. Repeat tTG-IgA, ferritin, and CBC in 3 months at a lab near home
8. Return for temperature over 100.4 F, inability to keep fluids down, or worsening belly pain
```

## ENTITIES - `mts-train-967` (mts-dialog)

<details><summary>Source note</summary>

```text
Patient: Oh doc, I've had a lot of trouble breathing lately.
Doctor: I'm sorry to hear that. We'll get to the bottom of it.
Patient: Yeah, I lose my breath so quickly with just one to two flights of stairs.
Doctor: When did you first start noticing this?
Patient: I believe two weeks ago.
Doctor: Any chest pain or tightness with the shortness of breath?
Patient: No, thankfully.
Doctor: Any fever, chills, or sweats?
Patient: No.
Doctor: Any cough or coughing up blood?
Patient: No.
Doctor: How about wheezing or swelling in your legs?
Patient: No.
```
</details>

**Reference**

```text
{
  "symptoms": [
    {
      "text": "trouble breathing",
      "negated": false
    },
    {
      "text": "lose my breath so quickly with just one to two flights of stairs",
      "negated": false
    },
    {
      "text": "chest pain",
      "negated": true
    },
    {
      "text": "tightness",
      "negated": true
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "chills",
      "negated": true
    },
    {
      "text": "sweats",
      "negated": true
    },
    {
      "text": "cough",
      "negated": true
    },
    {
      "text": "coughing up blood",
      "negated": true
    },
    {
      "text": "wheezing",
      "negated": true
    },
    {
      "text": "swelling in your legs",
      "negated": true
    }
  ],
  "diagnoses": [],
  "medications": [],
  "procedures": []
}
```

**Base (Qwen2.5-7B-Instruct)**

```text
{
  "symptoms": [
    {"text": "trouble breathing", "negated": false},
    {"text": "lose breath quickly with just one to two flights of stairs", "negated": false},
    {"text": "chest pain or tightness with the shortness of breath", "negated": true},
    {"text": "fever", "negated": true},
    {"text": "chills", "negated": true},
    {"text": "sweats", "negated": true},
    {"text": "cough", "negated": true},
    {"text": "coughing up blood", "negated": true},
    {"text": "wheezing", "negated": true},
    {"text": "swelling in your legs", "negated": true}
  ],
  "diagnoses": [],
  "medications": [],
  "procedures": []
}
```

**SFT**

```text
{
  "symptoms": [
    {
      "text": "trouble breathing",
      "negated": false
    },
    {
      "text": "lose my breath so quickly",
      "negated": false
    },
    {
      "text": "chest pain or tightness",
      "negated": true
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "chills",
      "negated": true
    },
    {
      "text": "sweats",
      "negated": true
    },
    {
      "text": "cough",
      "negated": true
    },
    {
      "text": "coughing up blood",
      "negated": true
    },
    {
      "text": "wheezing",
      "negated": true
    },
    {
      "text": "swelling in your legs",
      "negated": true
    }
  ],
  "diagnoses": [],
  "medications": [],
  "procedures": []
}
```

**SFT + ORPO**

```text
{
  "symptoms": [
    {
      "text": "trouble breathing",
      "negated": false
    },
    {
      "text": "lose my breath so quickly",
      "negated": false
    },
    {
      "text": "chest pain",
      "negated": true
    },
    {
      "text": "tightness",
      "negated": true
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "chills",
      "negated": true
    },
    {
      "text": "sweats",
      "negated": true
    },
    {
      "text": "cough",
      "negated": true
    },
    {
      "text": "coughing up blood",
      "negated": true
    },
    {
      "text": "wheezing",
      "negated": true
    },
    {
      "text": "swelling in your legs",
      "negated": true
    }
  ],
  "diagnoses": [],
  "medications": [],
  "procedures": []
}
```

## ENTITIES - `syn-00243` (synthetic)

<details><summary>Source note</summary>

```text
Neurology dictation, hospital day 3, on a 76-year-old female with relapsing remitting multiple sclerosis who was admitted with a suspected relapse. She was diagnosed with multiple sclerosis about twenty five years ago and has had two prior relapses, the last one eight years ago involving the left eye. About a week ago she developed numbness and weakness in the right leg with trouble walking, and over the next few days she noticed she was dragging the right foot and holding onto the walls at home. She also has bladder urgency and an electric shock sensation down her spine when she bends her neck forward. She denies fever, painful urination, headache, vision loss, double vision, difficulty swallowing, and new weakness in the arms. Because she has limited health literacy, I used plain language and teach-back with her throughout, and she describes her illness as her nerves getting angry again.

Her home medications are dimethyl fumarate 120 mg twice daily, correction, 240 mg twice daily, which I confirmed against the pharmacy list, baclofen 5 mg three times daily for leg stiffness, amlodipine 5 mg daily, and metformin 1000 mg twice daily. Past history also includes hypertension, type 2 diabetes, and osteoporosis. She has no known drug allergies.

On exam vitals are temperature 36.9 C, heart rate 78, blood pressure 152/84, respiratory rate 16, and oxygen saturation 97% on room air. She is alert and oriented with fluent speech. Strength is 3 out of 5 at the right hip flexor and ankle dorsiflexion and 5 out of 5 elsewhere. Reflexes are brisk in the legs with an upgoing toe on the right. There is reduced vibration and pinprick sensation in the right leg below the knee, and she walks only with a walker and a right foot drop.

Data review. Urinalysis was negative for nitrite and leukocyte esterase and the urine culture shows no growth, so there is no infection to explain a pseudo-relapse. WBC is 8.2, creatinine 0.8, and her glucose was 246 mg/dL this morning on steroids. Her absolute lymphocyte count is 0.7, which is low. MRI of the brain and cervical spine with and without gadolinium showed a new enhancing lesion at C3-C4 on the right side of the cord and several old periventricular lesions without enhancement.

Impression is an acute relapse of multiple sclerosis with a right sided cervical cord lesion, a breakthrough on dimethyl fumarate, with lymphopenia and steroid associated hyperglycemia.

Recommendations and plan. Continue methylprednisolone 1000 mg IV daily [...]
```
</details>

**Reference**

```text
{
  "symptoms": [
    {
      "text": "numbness",
      "negated": false
    },
    {
      "text": "weakness in the right leg",
      "negated": false
    },
    {
      "text": "trouble walking",
      "negated": false
    },
    {
      "text": "dragging the right foot",
      "negated": false
    },
    {
      "text": "bladder urgency",
      "negated": false
    },
    {
      "text": "electric shock sensation",
      "negated": false
    },
    {
      "text": "leg stiffness",
      "negated": false
    },
    {
      "text": "right foot drop",
      "negated": false
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "painful urination",
      "negated": true
    },
    {
      "text": "headache",
      "negated": true
    },
    {
      "text": "vision loss",
      "negated": true
    },
    {
      "text": "double vision",
      "negated": true
    },
    {
      "text": "difficulty swallowing",
      "negated": true
    },
    {
      "text": "new weakness in the arms",
      "negated": true
    }
  ],
  "diagnoses": [
    "relapsing remitting multiple sclerosis",
    "hypertension",
    "type 2 diabetes",
    "osteoporosis",
    "acute relapse of multiple sclerosis",
    "right sided cervical cord lesion",
    "lymphopenia",
    "steroid associated hyperglycemia"
  ],
  "medications": [
    {
      "name": "dimethyl fumarate",
      "dose": "240 mg",
      "frequency": "twice daily"
    },
    {
      "name": "baclofen",
      "dose": "10 mg",
      "frequency": "three times daily"
    },
    {
      "name": "amlodipine",
      "dose": "5 mg",
      "frequency": "daily"
    },
    {
      "name": "metformin",
      "dose": "1000 mg",
      "frequency": "twice daily"
    },
    {
      "name": "methylprednisolone",
      "dose": "1000 mg",
      "frequency": "daily"
    },
    {
      "name": "pantoprazole",
      "dose": "40 mg",
      "frequency": "daily"
    },
    {
      "name": "insulin lispro",
      "dose": "sliding scale",
      "frequency": "with meals"
    }
  ],
  "procedures": [
    "urinalysis",
    "urine culture",
    "MRI of the brain and cervical spine with and without gadolinium",
    "bladder scan",
    "physical therapy",
    "occupational therapy"
  ]
}
```

**Base (Qwen2.5-7B-Instruct)**

```text
```json
{
  "symptoms": [
    {"text": "numbness and weakness in the right leg", "negated": false},
    {"text": "trouble walking", "negated": false},
    {"text": "dragging the right foot", "negated": false},
    {"text": "holding onto the walls at home", "negated": false},
    {"text": "bladder urgency", "negated": false},
    {"text": "electric shock sensation down her spine when she bends her neck forward", "negated": false},
    {"text": "fever", "negated": true},
    {"text": "painful urination", "negated": true},
    {"text": "headache", "negated": true},
    {"text": "vision loss", "negated": true},
    {"text": "double vision", "negated": true},
    {"text": "difficulty swallowing", "negated": true},
    {"text": "new weakness in the arms", "negated": true}
  ],
  "diagnoses": [
    "relapsing remitting multiple sclerosis",
    "acute relapse of multiple sclerosis",
    "right sided cervical cord lesion",
    "lymphopenia",
    "steroid associated hyperglycemia"
  ],
  "medications": [
    {"name": "dimethyl fumarate", "dose": "120 mg", "frequency": "twice daily"},
    {"name": "correction", "dose": "240 mg", "frequency": "twice daily"},
    {"name": "baclofen", "dose": "5 mg", "frequency": "three times daily"},
    {"name": "amlodipine", "dose": "5 mg", "frequency": "daily"},
    {"name": "metformin", "dose": "1000 mg", "frequency": "twice daily"},
    {"name": "methylprednisolone", "dose": "1000 mg", "frequency": "IV daily"},
    {"name": "pantoprazole", "dose": "40 mg", "frequency": "daily"},
    {"name": "insulin lispro", "dose": null, "frequency": "on a sliding scale with meals"},
    {"name": "dimethyl fumarate", "dose": null, "frequency": "stop"}
  ],
  "procedures": [
    "MRI of the brain and cervical spine with and without gadolinium",
    "order a bladder scan for post void residual",
    "physical therapy",
    "occupational therapy",
    "fall precautions"
  ]
}
```
```

**SFT**

```text
{
  "symptoms": [
    {
      "text": "numbness",
      "negated": false
    },
    {
      "text": "weakness",
      "negated": false
    },
    {
      "text": "trouble walking",
      "negated": false
    },
    {
      "text": "dragging the right foot",
      "negated": false
    },
    {
      "text": "bladder urgency",
      "negated": false
    },
    {
      "text": "electric shock sensation",
      "negated": false
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "painful urination",
      "negated": true
    },
    {
      "text": "headache",
      "negated": true
    },
    {
      "text": "vision loss",
      "negated": true
    },
    {
      "text": "double vision",
      "negated": true
    },
    {
      "text": "difficulty swallowing",
      "negated": true
    },
    {
      "text": "new weakness in the arms",
      "negated": true
    }
  ],
  "diagnoses": [
    "relapsing remitting multiple sclerosis",
    "hypertension",
    "type 2 diabetes",
    "osteoporosis",
    "lymphopenia"
  ],
  "medications": [
    {
      "name": "dimethyl fumarate",
      "dose": "240 mg",
      "frequency": "twice daily"
    },
    {
      "name": "baclofen",
      "dose": "10 mg",
      "frequency": "three times daily"
    },
    {
      "name": "amlodipine",
      "dose": "5 mg",
      "frequency": "daily"
    },
    {
      "name": "metformin",
      "dose": "1000 mg",
      "frequency": "twice daily"
    },
    {
      "name": "methylprednisolone",
      "dose": "1000 mg",
      "frequency": "daily"
    },
    {
      "name": "pantoprazole",
      "dose": "40 mg",
      "frequency": "daily"
    },
    {
      "name": "insulin lispro",
      "dose": null,
      "frequency": "with meals"
    }
  ],
  "procedures": [
    "Urinalysis",
    "urine culture",
    "WBC",
    "creatinine",
    "glucose",
    "absolute lymphocyte count",
    "MRI of the brain and cervical spine with and without gadolinium",
    "bladder scan"
  ]
}
```

**SFT + ORPO**

```text
{
  "symptoms": [
    {
      "text": "numbness",
      "negated": false
    },
    {
      "text": "weakness",
      "negated": false
    },
    {
      "text": "trouble walking",
      "negated": false
    },
    {
      "text": "dragging the right foot",
      "negated": false
    },
    {
      "text": "bladder urgency",
      "negated": false
    },
    {
      "text": "electric shock sensation",
      "negated": false
    },
    {
      "text": "fever",
      "negated": true
    },
    {
      "text": "painful urination",
      "negated": true
    },
    {
      "text": "headache",
      "negated": true
    },
    {
      "text": "vision loss",
      "negated": true
    },
    {
      "text": "double vision",
      "negated": true
    },
    {
      "text": "difficulty swallowing",
      "negated": true
    },
    {
      "text": "new weakness in the arms",
      "negated": true
    }
  ],
  "diagnoses": [
    "relapsing remitting multiple sclerosis",
    "hypertension",
    "type 2 diabetes",
    "osteoporosis",
    "lymphopenia"
  ],
  "medications": [
    {
      "name": "dimethyl fumarate",
      "dose": "240 mg",
      "frequency": "twice daily"
    },
    {
      "name": "baclofen",
      "dose": "10 mg",
      "frequency": "three times daily"
    },
    {
      "name": "amlodipine",
      "dose": "5 mg",
      "frequency": "daily"
    },
    {
      "name": "metformin",
      "dose": "1000 mg",
      "frequency": "twice daily"
    },
    {
      "name": "methylprednisolone",
      "dose": "1000 mg",
      "frequency": "daily"
    },
    {
      "name": "pantoprazole",
      "dose": "40 mg",
      "frequency": "daily"
    },
    {
      "name": "insulin lispro",
      "dose": null,
      "frequency": "with meals"
    }
  ],
  "procedures": [
    "Urinalysis",
    "urine culture",
    "WBC",
    "creatinine",
    "glucose",
    "absolute lymphocyte count",
    "MRI of the brain and cervical spine with and without gadolinium",
    "bladder scan"
  ]
}
```
