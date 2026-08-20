const TOPIC_BANKS = [
  {
    keywords: ["school", "study", "college", "university", "student", "class", "course", "exam", "homework", "academic"],
    names: ["Lectures", "Assignments", "Exams", "Study Group", "Reading", "Projects", "Office Hours", "Deadlines"],
  },
  {
    keywords: ["work", "job", "business", "office", "career", "client", "meeting", "startup", "company"],
    names: ["Meetings", "Deep Work", "Emails", "Projects", "Admin", "Reports", "Client Calls", "Travel"],
  },
  {
    keywords: ["fitness", "gym", "workout", "health", "exercise", "sport", "training", "run", "running"],
    names: ["Workout", "Cardio", "Strength", "Yoga", "Rest Day", "Nutrition", "Stretching", "Sports"],
  },
  {
    keywords: ["family", "home", "house", "kids", "chores", "household"],
    names: ["Chores", "Kids", "Errands", "Cooking", "Family Time", "Cleaning", "Maintenance", "Pets"],
  },
  {
    keywords: ["travel", "trip", "vacation", "holiday"],
    names: ["Flights", "Hotels", "Sightseeing", "Packing", "Itinerary", "Budget", "Documents"],
  },
  {
    keywords: ["finance", "money", "budget", "invest", "investing"],
    names: ["Budgeting", "Bills", "Savings", "Investing", "Expenses", "Taxes", "Income"],
  },
  {
    keywords: ["personal", "self", "hobby", "hobbies", "life"],
    names: ["Errands", "Reading", "Hobbies", "Self Care", "Social", "Appointments", "Goals"],
  },
];

function pickBank(topic) {
  const t = topic.toLowerCase();
  let best = null;
  let bestScore = 0;
  for (const bank of TOPIC_BANKS) {
    const score = bank.keywords.filter((k) => t.includes(k)).length;
    if (score > bestScore) {
      bestScore = score;
      best = bank;
    }
  }
  return best;
}

function titleCase(str) {
  return str
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const GENERIC_SUFFIXES = ["Planning", "Tasks", "Events", "Follow-up", "Ideas", "Review", "Notes", "Misc"];

/**
 * Suggests up to `count` category names relevant to `topic`. Categories are
 * plain labels — color is chosen per task/event, not per category — so this
 * only ever needs to propose names.
 */
export function suggestCategories(topic, count) {
  const bank = pickBank(topic || "");

  let names;
  if (bank) {
    names = [...bank.names];
  } else if (topic.trim()) {
    const base = titleCase(topic);
    names = count <= 1 ? [base] : GENERIC_SUFFIXES.map((s) => `${base} ${s}`);
  } else {
    names = GENERIC_SUFFIXES.map((s, i) => `Category ${i + 1}`);
  }
  while (names.length < count) names.push(`Category ${names.length + 1}`);

  return names.slice(0, Math.max(0, count));
}
